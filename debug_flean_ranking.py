#!/usr/bin/env python3
"""
debug_flean_ranking.py — TEMPORARY diagnostic CLI for Flean Score reranking.
Delete once the investigation is complete; not part of the production
request path, nothing else imports it.

Runs the exact same production functions the gateway uses
(process_search_request(), hybrid_search(), apply_business_ranking()) with no
modification to any of them, and prints, for each candidate:

  - its rank BEFORE business ranking (pure relevance / fused_score order)
  - its rank AFTER business ranking (final_score order)
  - relevance_score, business_multiplier, final_score
  - the flean_nutrition_rule component specifically (out of the full
    rule_breakdown), so the Flean contribution is visible in isolation

Then reports which items actually changed rank and by how much, so the
before/after effect of Flean reranking is directly observable rather than
inferred from the formula alone.

Read-only against OpenSearch/MongoDB. Requires the same environment as
dev_search_cli.py / debug_product_intent.py.

Usage:
    python3 debug_flean_ranking.py "healthy chips"
    python3 debug_flean_ranking.py "banana chips" "greek yogurt" "granola bar" "protein bar"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("queries", nargs="+")
    parser.add_argument("--size", type=int, default=10, help="Page size shown (default 10)")
    args = parser.parse_args()

    print("Initializing Search V2 pipeline (this hits your real cluster)...", flush=True)

    from search_v2.config.settings import SETTINGS
    from search_v2.embedding.embedding_service import get_embedding_service
    from search_v2.query_processing.query_pipeline import process_search_request
    from search_v2.query_processing.typo_correction import VocabularyCorrector
    from search_v2.query_processing.vocabulary_builder import VOCABULARY_PATH, load_vocabulary, seed_vocabulary
    from search_v2.query_processing.product_intent_extractor import (
        PRODUCT_TYPE_LEXICON_PATH, ProductIntentExtractor, load_product_type_lexicon,
    )
    from search_v2.query_processing.canonical_produce import (
        PRODUCE_SYNONYMS_PATH, load_produce_alias_map,
    )
    from search_v2.ranking.business_ranking import apply_business_ranking
    from search_v2.retrieval.hybrid_search_orchestrator import hybrid_search
    from search_v2.retrieval.opensearch_client import OpenSearchClient

    client = OpenSearchClient(settings=SETTINGS)
    emb_svc = get_embedding_service(SETTINGS.EMBEDDING_MODEL_KEY)
    vocab = load_vocabulary(VOCABULARY_PATH) or seed_vocabulary()
    corrector = VocabularyCorrector(vocab)
    lexicon = load_product_type_lexicon(PRODUCT_TYPE_LEXICON_PATH)
    produce_aliases = load_produce_alias_map(PRODUCE_SYNONYMS_PATH)
    extractor = ProductIntentExtractor(lexicon, settings=SETTINGS, produce_aliases=produce_aliases)

    print(f"ENABLE_BUSINESS_RANKING={SETTINGS.ENABLE_BUSINESS_RANKING}  "
          f"BUSINESS_MIN_MULTIPLIER={SETTINGS.BUSINESS_MIN_MULTIPLIER}  "
          f"BUSINESS_MAX_MULTIPLIER={SETTINGS.BUSINESS_MAX_MULTIPLIER}\n")

    for raw_q in args.queries:
        print(f"\n{'=' * 100}")
        print(f"QUERY: {raw_q!r}")
        print("=" * 100)

        req = process_search_request(
            raw_q, corrector=corrector, enable_typo_correction=SETTINGS.ENABLE_TYPO_CORRECTION,
            product_intent_extractor=extractor, settings=SETTINGS,
        )
        pi = req.product_intent
        print(f"product_intent: primary_product={getattr(pi, 'primary_product', None)!r} "
              f"tier={getattr(pi, 'tier', None)!r} mode={req.filters.product_type_mode!r}")

        hybrid_result = hybrid_search(client, req.processed_query, req.filters, args.size, SETTINGS, emb_svc)
        print(f"retrieval candidate pool size: {len(hybrid_result.items)} "
              f"(business ranking below does NOT change this number)")

        # Rank BEFORE business ranking = pure relevance order (fused_score desc)
        before_order = sorted(hybrid_result.items, key=lambda it: it.fused_score, reverse=True)
        before_rank = {id(it): i + 1 for i, it in enumerate(before_order)}

        ranked = apply_business_ranking(hybrid_result.items, subcategory="_default", settings=SETTINGS)

        print(f"\n{'rank_before':>11s} {'rank_after':>10s} {'move':>5s}  {'relevance':>10s} {'flean_mult':>10s} "
              f"{'final':>10s}  name")
        moved_count = 0
        for after_idx, item in enumerate(ranked, start=1):
            # match back to the pre-business-ranking ResultItem by doc_id to find its "before" rank
            before_idx = None
            for orig in before_order:
                if getattr(orig, "doc_id", None) == item.doc_id:
                    before_idx = before_rank[id(orig)]
                    break
            move = (before_idx - after_idx) if before_idx is not None else 0
            if move != 0:
                moved_count += 1
            flean_component = item.rule_breakdown.get("flean_nutrition_rule", 1.0)
            name = (item.source or {}).get("name", "?")
            marker = f"{move:+d}" if move != 0 else "  ."
            print(f"{before_idx!s:>11s} {after_idx:>10d} {marker:>5s}  {item.relevance_score:>10.4f} "
                  f"{flean_component:>10.4f} {item.final_score:>10.4f}  {name[:70]}")
            if after_idx >= args.size:
                break

        print(f"\n{moved_count} of the shown items changed rank position due to business ranking "
              f"(Flean + other DEFAULT_RULES combined, bounded multiplier "
              f"[{SETTINGS.BUSINESS_MIN_MULTIPLIER}, {SETTINGS.BUSINESS_MAX_MULTIPLIER}]).")


if __name__ == "__main__":
    main()
