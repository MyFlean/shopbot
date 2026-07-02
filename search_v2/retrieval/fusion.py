"""
search_v2/retrieval/fusion.py
─────────────────────────────────────────
Pure fusion math — no HTTP, no OpenSearch client, deliberately. Combining
ranked/scored result lists from lexical and semantic retrieval is just data
transformation; keeping it dependency-free here means it's fully unit-testable
without any live cluster (see tests/test_fusion.py) and reusable from the
playground/benchmarking milestones without dragging in client code.

Three strategies, selected via search_v2.config.SETTINGS.FUSION_STRATEGY:

  "rrf" (Reciprocal Rank Fusion)  — DEFAULT for Search V2. Rank-based, not
      score-based: combines 1/(k + rank) across retrievers. This is what's
      actually recommended here, and here's the reasoning (see
      ARCHITECTURE.md for the full writeup):
        - BM25 scores are unbounded and corpus/query-statistics-dependent;
          cosine similarity is bounded [0,1]. Fusing them by weighted SUM
          requires normalizing both onto a comparable scale, and that
          normalization is itself a source of fragility (per-query min/max
          can swing wildly depending on how many terms matched). RRF sidesteps
          this entirely — it never looks at the raw scores, only rank
          position, so the two retrievers' wildly different score
          distributions are simply not a problem.
        - It needs ZERO cluster-side configuration (no search pipeline to
          provision) and works identically on ANY OpenSearch/Elasticsearch
          version — unlike OpenSearch's native hybrid-query RRF support
          (score-ranker-processor), which only shipped in OpenSearch 2.19+
          (the production domain, per OPENSEARCH-MIGRATION-2026-04.md, is on
          2.15 — see Search V1's ARCHITECTURE.md for that finding, which
          still applies here).
        - It's pure Python, fully unit-testable, and trivially explainable —
          all properties this is meant to be the long-term architecture for.
      Cost: two round trips (lexical query + semantic query) instead of one.
      At this catalog's actual scale (~8K products, <1 QPS — see Search V1's
      findings), that cost is immaterial.

  "weighted" — score-based: min-max normalize each retriever's scores
      per-query, then combine via a weighted sum (SETTINGS.FUSION_WEIGHTS).
      Included for comparison in the benchmarking milestone — this is
      legitimately a reasonable choice too, just more sensitive to per-query
      score-distribution quirks than RRF.

  "native_hybrid" — OpenSearch's own `hybrid` query + a `normalization-processor`
      search pipeline (min_max normalization + weighted arithmetic_mean —
      NOT RRF, since the score-ranker-processor needs 2.19+). Single round
      trip, lowest latency, but requires the search pipeline to exist on the
      cluster and ties the implementation to that specific OpenSearch version's
      feature set. See retrieval/hybrid_query_builder.py for the query-building
      side of this strategy — fusion.py only implements the two purely-Python
      strategies, since "native_hybrid" fusion happens inside OpenSearch, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class FusedResult:
    doc_id: str
    fused_score: float
    lexical_rank: Optional[int] = None
    lexical_score: Optional[float] = None
    semantic_rank: Optional[int] = None
    semantic_score: Optional[float] = None


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    rank_constant: int = 60,
    weights: Optional[Sequence[float]] = None,
    raw_scores: Optional[Sequence[Sequence[float]]] = None,
) -> List[FusedResult]:
    """
    `ranked_lists`: one ranked list of doc_ids per retriever, e.g.
        [lexical_ranked_ids, semantic_ranked_ids]
    `weights`: optional per-retriever weight (default: equal weight 1.0 each).
    `raw_scores`: optional, same shape as ranked_lists — if provided, the
        original per-retriever scores are carried through onto FusedResult
        for display (e.g. the playground's "lexical score / semantic score /
        hybrid score" breakdown) even though they don't affect the RRF math
        itself (RRF is rank-only, by design — see module docstring).

    Standard RRF formula: score(d) = sum_i weight_i * 1/(rank_constant + rank_i(d))
    A doc not present in a given retriever's list contributes 0 from that
    retriever (not penalized further) — it's simply absent, treated as
    "infinitely ranked" only in the sense that it gets no credit, not a
    negative one.
    """
    weights = list(weights) if weights else [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must have the same length as ranked_lists")

    fused: Dict[str, float] = {}
    lexical_info: Dict[str, Tuple[int, Optional[float]]] = {}
    semantic_info: Dict[str, Tuple[int, Optional[float]]] = {}

    for retriever_idx, ranked_list in enumerate(ranked_lists):
        weight = weights[retriever_idx]
        scores_for_this_list = raw_scores[retriever_idx] if raw_scores else None
        for rank, doc_id in enumerate(ranked_list, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + weight * (1.0 / (rank_constant + rank))
            score = scores_for_this_list[rank - 1] if scores_for_this_list else None
            if retriever_idx == 0:
                lexical_info[doc_id] = (rank, score)
            elif retriever_idx == 1:
                semantic_info[doc_id] = (rank, score)

    results = []
    for doc_id, score in fused.items():
        lex_rank, lex_score = lexical_info.get(doc_id, (None, None))
        sem_rank, sem_score = semantic_info.get(doc_id, (None, None))
        results.append(FusedResult(
            doc_id=doc_id, fused_score=score,
            lexical_rank=lex_rank, lexical_score=lex_score,
            semantic_rank=sem_rank, semantic_score=sem_score,
        ))

    results.sort(key=lambda r: r.fused_score, reverse=True)
    return results


def _min_max_normalize(scores: Sequence[float]) -> List[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-12:
        return [1.0 for _ in scores]  # all identical — avoid div-by-zero, treat as equally relevant
    return [(s - lo) / (hi - lo) for s in scores]


def weighted_score_fusion(
    scored_lists: Sequence[Sequence[Tuple[str, float]]],
    weights: Optional[Sequence[float]] = None,
) -> List[FusedResult]:
    """
    `scored_lists`: one (doc_id, raw_score) ranked list per retriever, e.g.
        [lexical_scored_hits, semantic_scored_hits]
    Each list's scores are min-max normalized to [0, 1] INDEPENDENTLY (per
    retriever, per query — matching what OpenSearch's own normalization-
    processor does for the native_hybrid strategy) before the weighted sum,
    specifically so the two retrievers' very different raw score scales
    (unbounded BM25 vs bounded cosine similarity) don't make one silently
    dominate just because its numbers happen to be bigger.
    """
    weights = list(weights) if weights else [1.0] * len(scored_lists)
    if len(weights) != len(scored_lists):
        raise ValueError("weights must have the same length as scored_lists")

    fused: Dict[str, float] = {}
    lexical_info: Dict[str, Tuple[int, float]] = {}
    semantic_info: Dict[str, Tuple[int, float]] = {}

    for retriever_idx, scored_list in enumerate(scored_lists):
        if not scored_list:
            continue
        doc_ids = [d for d, _ in scored_list]
        raw_scores = [s for _, s in scored_list]
        normalized = _min_max_normalize(raw_scores)
        weight = weights[retriever_idx]

        for rank, (doc_id, norm_score) in enumerate(zip(doc_ids, normalized), start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + weight * norm_score
            if retriever_idx == 0:
                lexical_info[doc_id] = (rank, raw_scores[rank - 1])
            elif retriever_idx == 1:
                semantic_info[doc_id] = (rank, raw_scores[rank - 1])

    results = []
    for doc_id, score in fused.items():
        lex_rank, lex_score = lexical_info.get(doc_id, (None, None))
        sem_rank, sem_score = semantic_info.get(doc_id, (None, None))
        results.append(FusedResult(
            doc_id=doc_id, fused_score=score,
            lexical_rank=lex_rank, lexical_score=lex_score,
            semantic_rank=sem_rank, semantic_score=sem_score,
        ))

    results.sort(key=lambda r: r.fused_score, reverse=True)
    return results
