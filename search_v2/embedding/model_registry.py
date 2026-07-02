"""
search_v2/embedding/model_registry.py
─────────────────────────────────────────
Candidate embedding models evaluated for Search V2, with the metadata needed
to compare them (size, dim, license, latency/memory class) and the reasoning
behind the production recommendation. See docs/EMBEDDING_MODEL_EVALUATION.md
for the full writeup — this file is the machine-readable side of that doc.

IMPORTANT — what's actually been verified vs. what's a documented estimate:
This sandbox has no network access, so none of these models have been
downloaded or run here. Sizes/dims/licenses are from public model cards
(stable, version-controlled facts). Latency/recall/precision numbers are
NOT filled in below — run benchmarking/benchmark_embedding_models.py in an
environment with network + your real catalog to populate those for real
before trusting any "winner" claim. The RECOMMENDED flag below reflects a
reasoned-but-unverified starting point, not a benchmarked conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class EmbeddingModelSpec:
    key: str                       # used in SEARCH_V2_EMBEDDING_MODEL env var
    hf_name: str                   # sentence-transformers / HF hub identifier
    dim: int
    params_millions: float
    approx_disk_mb: int
    license: str
    asymmetric: bool                # True = needs different query: / passage: prefixing
    query_prefix: str
    passage_prefix: str
    multilingual: bool
    notes: str
    recommended: bool = False


MODEL_REGISTRY: Dict[str, EmbeddingModelSpec] = {
    "e5-small-v2": EmbeddingModelSpec(
        key="e5-small-v2",
        hf_name="intfloat/e5-small-v2",
        dim=384,
        params_millions=33,
        approx_disk_mb=130,
        license="MIT",
        asymmetric=True,
        query_prefix="query: ",
        passage_prefix="passage: ",
        multilingual=False,
        notes=(
            "The current Search V1 choice (and what's live in es_products.py / "
            "shopping_bot/search/embedding_service.py today). Fast and cheap, but "
            "the smallest-capacity model in this list. Kept here as the control "
            "/ baseline for benchmarking, not as the V2 default."
        ),
    ),
    "bge-small-en-v1.5": EmbeddingModelSpec(
        key="bge-small-en-v1.5",
        hf_name="BAAI/bge-small-en-v1.5",
        dim=384,
        params_millions=33,
        approx_disk_mb=130,
        license="MIT",
        asymmetric=True,
        query_prefix="Represent this sentence for searching relevant passages: ",
        passage_prefix="",
        multilingual=False,
        notes="Same size class as e5-small-v2, frequently a notch ahead on retrieval "
              "benchmarks at the same dimension. Useful same-cost comparison point.",
    ),
    "bge-base-en-v1.5": EmbeddingModelSpec(
        key="bge-base-en-v1.5",
        hf_name="BAAI/bge-base-en-v1.5",
        dim=768,
        params_millions=109,
        approx_disk_mb=420,
        license="MIT",
        asymmetric=True,
        query_prefix="Represent this sentence for searching relevant passages: ",
        passage_prefix="",
        multilingual=False,
        notes=(
            "RECOMMENDED V2 DEFAULT (pending your empirical benchmark run — see module "
            "docstring). ~3x the parameters of e5-small-v2 but still comfortably "
            "CPU-feasible at this catalog's scale (~8K products) and <1 QPS traffic. "
            "768d typically gives a meaningful retrieval-quality step up over 384d "
            "small models on short e-commerce-style text. Going further to a "
            "1024d/large model (see bge-large-en-v1.5 below) is the next rung up in "
            "quality but with materially worse cost/latency for, at this corpus size, "
            "likely diminishing returns — that trade-off is exactly what "
            "benchmark_embedding_models.py is for."
        ),
        recommended=True,
    ),
    "e5-base-v2": EmbeddingModelSpec(
        key="e5-base-v2",
        hf_name="intfloat/e5-base-v2",
        dim=768,
        params_millions=109,
        approx_disk_mb=420,
        license="MIT",
        asymmetric=True,
        query_prefix="query: ",
        passage_prefix="passage: ",
        multilingual=False,
        notes="Same tier as bge-base-en-v1.5 (size/dim/quality class). Worth "
              "benchmarking both since the better one varies by domain/text style.",
    ),
    "bge-large-en-v1.5": EmbeddingModelSpec(
        key="bge-large-en-v1.5",
        hf_name="BAAI/bge-large-en-v1.5",
        dim=1024,
        params_millions=335,
        approx_disk_mb=1340,
        license="MIT",
        asymmetric=True,
        query_prefix="Represent this sentence for searching relevant passages: ",
        passage_prefix="",
        multilingual=False,
        notes=(
            "Top-of-range quality in this family, but ~3x bge-base's params and "
            "~10x e5-small's. At an ~8K-document corpus the discrimination problem "
            "is already easy for a base-sized model (the long tail of near-duplicate "
            "confusable products that large models help most with matters far more "
            "at million-scale catalogs). Include in the benchmark to confirm, but "
            "don't default to this without it earning its cost."
        ),
    ),
    "snowflake-arctic-embed-m": EmbeddingModelSpec(
        key="snowflake-arctic-embed-m",
        hf_name="Snowflake/snowflake-arctic-embed-m",
        dim=768,
        params_millions=109,
        approx_disk_mb=420,
        license="Apache-2.0",
        asymmetric=True,
        query_prefix="Represent this sentence for searching relevant passages: ",
        passage_prefix="",
        multilingual=False,
        notes="Same size/dim class as bge-base, different training recipe — Apache-2.0 "
              "is a slightly more permissive license than MIT in a couple of edge "
              "cases (patent grant). Worth a benchmark run alongside bge-base.",
    ),
    "multilingual-e5-base": EmbeddingModelSpec(
        key="multilingual-e5-base",
        hf_name="intfloat/multilingual-e5-base",
        dim=768,
        params_millions=278,
        approx_disk_mb=1060,
        license="MIT",
        asymmetric=True,
        query_prefix="query: ",
        passage_prefix="passage: ",
        multilingual=True,
        notes=(
            "NOT recommended despite the Hindi-query use case. Multilingual "
            "embedding training data is overwhelmingly NATIVE-SCRIPT text "
            "(Devanagari Hindi), not romanized/transliterated Hindi typed in Latin "
            "script ('seb', 'tarbuj', 'doodh') — which is what your users actually "
            "type. This model doesn't reliably close that gap, costs 2.5x bge-base's "
            "params for it, and the romanized-Hindi problem is much more reliably "
            "solved by the synonym/normalization layer (query_processing/ + the "
            "Roman Hindi CSV) than by hoping embedding similarity bridges the script "
            "gap. Kept in the registry so this claim is itself benchmarkable, not "
            "just asserted."
        ),
    ),
}


def get_model_spec(key: str) -> EmbeddingModelSpec:
    if key not in MODEL_REGISTRY:
        valid = ", ".join(MODEL_REGISTRY.keys())
        raise KeyError(f"Unknown embedding model key '{key}'. Valid keys: {valid}")
    return MODEL_REGISTRY[key]


def get_recommended_model() -> EmbeddingModelSpec:
    for spec in MODEL_REGISTRY.values():
        if spec.recommended:
            return spec
    raise RuntimeError("No model in MODEL_REGISTRY is marked recommended=True")


def default_model_for_benchmark() -> Optional[EmbeddingModelSpec]:
    """The model SETTINGS.EMBEDDING_MODEL_KEY currently points at."""
    from search_v2.config.settings import SETTINGS
    return MODEL_REGISTRY.get(SETTINGS.EMBEDDING_MODEL_KEY)
