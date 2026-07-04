#!/usr/bin/env python3
"""
debug_product_intent.py — TEMPORARY diagnostic CLI for Product Intent
Identification. Delete this file once the investigation is complete; it is
not part of the production request path and nothing else imports it.

Runs the exact same production functions the gateway uses
(process_search_request(), _hybrid_search_once(), lexical_query_builder,
semantic_query_builder) directly, with no modification to any of them, and
prints the fields needed to trace why a document was or wasn't admitted:

  - clean_query                          (ProcessedQuery.primary_text())
  - resolved product_type / confidence / tier
  - product_type_mode (filter / boost / none)
  - whether the built LEXICAL query body actually contains a product_type clause
  - whether the built SEMANTIC (kNN) query body actually contains a product_type pre-filter
  - hit count from the FIRST (gated) retrieval attempt
  - whether cascading relaxation would fire, and the hit count after it if so
  - the first N result names/ids from both the gated and (if applicable) relaxed pool,
    with a per-document breakdown against the raw catalog fields (product_type /
    product_type_confidence / category_hierarchies) when --show-docs is used

This is READ-ONLY against OpenSearch/MongoDB — it issues search queries only,
never writes. Requires the same environment variables dev_search_cli.py needs
(.env with ES_URL / SEARCH_V2_ES_URL / MONGO_URI etc.) since determining real
hit counts and real confidence scores requires the real cluster/catalog.

Usage:
    python3 debug_product_intent.py "chips under 300 calories"
    python3 debug_product_intent.py "granola bar" "sugar free drink"
    python3 debug_product_intent.py --show-docs "chips under 300 calories"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _contains_product_type_clause(query_body: dict) -> bool:
    """True if the literal string "product_type" appears anywhere in the
    built OpenSearch query body — the simplest, least-assumption-laden way
    to confirm a product_type clause is actually present in what will be
    sent to the cluster, rather than inferring it from Python-side state."""
    return '"product_type"' in json.dumps(query_body)


def _contains_id_terms_clause(query_body: dict) -> bool:
    """True if a {"terms": {"id": [...]}} clause (Fresh Produce hard
    id-restriction — see SearchFilters.product_ids) appears anywhere in the
    built query body."""
    return '"terms":{"id"' in json.dumps(query_body, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("queries", nargs="+", help="One or more raw queries to trace")
    parser.add_argument("--size", type=int, default=10, help="Page size (default 10, matches gateway default-ish)")
    parser.add_argument("--show-docs", action="store_true", help="Print product_type/category fields for the top hits")
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
    from search_v2.retrieval import lexical_query_builder, semantic_query_builder
    from search_v2.retrieval.filters import SearchFilters
    from search_v2.retrieval.hybrid_search_orchestrator import (
        _hybrid_search_once, _relax_product_type_filter, _wants_expanded_pool,
    )
    from search_v2.retrieval.opensearch_client import OpenSearchClient

    client = OpenSearchClient(settings=SETTINGS)
    emb_svc = get_embedding_service(SETTINGS.EMBEDDING_MODEL_KEY)

    vocab = load_vocabulary(VOCABULARY_PATH) or seed_vocabulary()
    corrector = VocabularyCorrector(vocab)

    lexicon = load_product_type_lexicon(PRODUCT_TYPE_LEXICON_PATH)
    print(f"Loaded product_type_lexicon.json: {len(lexicon)} terms "
          f"({'MISSING/EMPTY — Product Intent Identification will no-op for every query' if not lexicon else 'OK'})")
    produce_aliases = load_produce_alias_map(PRODUCE_SYNONYMS_PATH)
    print(f"Loaded produce_synonyms.json: {len(produce_aliases)} aliases")
    extractor = ProductIntentExtractor(lexicon, settings=SETTINGS, produce_aliases=produce_aliases)

    print(f"ENABLE_PRODUCT_INTENT={SETTINGS.ENABLE_PRODUCT_INTENT}  "
          f"HIGH={SETTINGS.PRODUCT_INTENT_HIGH_CONFIDENCE}  LOW={SETTINGS.PRODUCT_INTENT_LOW_CONFIDENCE}  "
          f"RELAXATION={SETTINGS.ENABLE_PRODUCT_INTENT_RELAXATION}  "
          f"MAX_POOL={SETTINGS.PRODUCT_INTENT_MAX_POOL_SIZE}  RETRIEVAL_K={SETTINGS.RETRIEVAL_K}")

    for raw_q in args.queries:
        print(f"\n{'=' * 90}")
        print(f"QUERY: {raw_q!r}")
        print("=" * 90)

        req = process_search_request(
            raw_q,
            corrector=corrector,
            enable_typo_correction=SETTINGS.ENABLE_TYPO_CORRECTION,
            product_intent_extractor=extractor,
            settings=SETTINGS,
        )

        clean_query = req.processed_query.primary_text()
        print(f"clean_query (post NL-filter + typo-correction) : {clean_query!r}")

        pi = req.product_intent
        if pi is None:
            print("product_intent                                  : NOT COMPUTED "
                  "(extractor disabled, ENABLE_PRODUCT_INTENT=false, or empty head text)")
        else:
            print(f"resolved product_type                            : {pi.primary_product!r}")
            print(f"modifiers                                        : {pi.modifiers!r}")
            print(f"confidence                                       : {pi.confidence:.4f}")
            print(f"confidence tier                                  : {pi.tier}")
            print(f"dominant_category                                : {pi.dominant_category!r}")
            print(f"fresh_produce_ids (Fresh Produce Identification) : "
                  f"{len(pi.fresh_produce_ids)} ids" if pi.fresh_produce_ids else "fresh_produce_ids                                : (none)")

        print(f"filters.product_type                             : {req.filters.product_type!r}")
        print(f"filters.product_type_mode                        : {req.filters.product_type_mode!r}")
        print(f"filters.product_type_category                    : {req.filters.product_type_category!r}")
        print(f"filters.product_ids (Fresh Produce hard filter)  : "
              f"{len(req.filters.product_ids)} ids" if req.filters.product_ids else "filters.product_ids                               : None")
        print(f"filters.macro_filters (NL-extracted)             : {req.filters.macro_filters!r}")
        print(f"filters.dietary_labels (NL-extracted)            : {req.filters.dietary_labels!r}")

        # ── Build the ACTUAL query bodies and check for the literal clause ──
        lexical_body = lexical_query_builder.build_query(req.processed_query, req.filters, args.size, SETTINGS)
        semantic_body = semantic_query_builder.build_query(req.processed_query, req.filters, args.size, SETTINGS, emb_svc)

        print(f"lexical query body contains \"product_type\"       : {_contains_product_type_clause(lexical_body)}")
        print(f"lexical query body contains id-terms hard filter : {_contains_id_terms_clause(lexical_body)}")
        if semantic_body is None:
            print(f"semantic query body contains \"product_type\"      : N/A (embedding model unavailable, semantic_body=None)")
        else:
            print(f"semantic query body contains \"product_type\"      : {_contains_product_type_clause(semantic_body)}")
            print(f"semantic query body contains id-terms hard filter: {_contains_id_terms_clause(semantic_body)}")

        # ── Run the FIRST (gated) retrieval attempt exactly as hybrid_search() does ──
        gated_result = _hybrid_search_once(client, req.processed_query, req.filters, args.size, SETTINGS, emb_svc)
        print(f"[gated attempt] pool size (len(items))           : {len(gated_result.items)}")
        print(f"[gated attempt] strategy_used                    : {gated_result.strategy_used}")

        wants_expanded = _wants_expanded_pool(req.filters)
        would_relax = (
            getattr(SETTINGS, "ENABLE_PRODUCT_INTENT_RELAXATION", True)
            and not getattr(SETTINGS, "STRICT_ZERO_RESULTS", False)
            and wants_expanded
            and not gated_result.items
        )
        print(f"_wants_expanded_pool(filters) (i.e. hard filter active) : {wants_expanded}")
        print(f"STRICT_ZERO_RESULTS                              : {getattr(SETTINGS, 'STRICT_ZERO_RESULTS', False)}")
        print(f"cascading relaxation would fire                  : {would_relax}")

        final_result = gated_result
        if would_relax:
            relaxed_filters = _relax_product_type_filter(req.filters)
            relaxed_result = _hybrid_search_once(client, req.processed_query, relaxed_filters, args.size, SETTINGS, emb_svc)
            print(f"[relaxed attempt] pool size (len(items))         : {len(relaxed_result.items)}")
            final_result = relaxed_result

        print(f"\nFINAL POOL SIZE RETURNED TO CALLER               : {len(final_result.items)}")
        print(f"(this is what business ranking + pagination operate on downstream)\n")

        print(f"Top {min(args.size, len(final_result.items))} in the FINAL pool (pre-business-ranking order):")
        for rank, item in enumerate(final_result.items[:args.size], 1):
            src = item.source or {}
            name = src.get("name", "?")
            pt = src.get("product_type")
            pt_conf = src.get("product_type_confidence")
            cat_hier = src.get("category_hierarchies") or []
            leaves = [seg.get("segments", [None])[-1] for seg in cat_hier if isinstance(seg, dict) and seg.get("segments")]
            print(f"  {rank:>2}. {name!r:60s} product_type={pt!r} conf={pt_conf} leaves={leaves}")
            if args.show_docs:
                print(f"      lexical_score={item.lexical_score} semantic_score={item.semantic_score} fused_score={item.fused_score}")


if __name__ == "__main__":
    main()
