# Search V2 (ShopBot)

Search V2 is the only search engine in ShopBot. All REST and chat product-search
paths call `search_v2.extension.*` modules directly.

For indexing, vocabulary generation, and OpenSearch mapping, see the companion
document in the `search` repository: `search/search_v2/README.md`.

## Architecture

```
HTTP routes (shopping_bot/routes/*)
        │
        ▼
search_v2/extension/*     ← feature-specific entry points
  search/                 query-driven hybrid search
  suggestions/            type-ahead
  category_browsing/      subcategory browse
  pdp/                    product detail fetch
  recommendations/        alternatives / similar
  bestsellers/ curated/ flean_picks/ …
        │
        ▼
search_v2/retrieval/*     ← query builders, OpenSearch client, fusion
search_v2/ranking/*       ← business + nutrition ranking
search_v2/query_processing/*  ← typo correction, filter extraction
search_v2/embedding/*     ← Bedrock Titan query embeddings
        │
        ▼
OpenSearch index (SEARCH_V2_INDEX_NAME, default products-search-v2)
```

Shared response transforms live in `shopping_bot/product_transforms.py`
(`transform_to_product_card`, `transform_to_pdp`).

Chat/conversational search uses `shopping_bot/data_fetchers/search_products.py`,
which plans params from LLM context and calls `search_v2.extension.search.search()`.

## Key routes

| Route file | Endpoints | Extension |
|---|---|---|
| `unified_search.py` | `/rs/v1/search`, suggest variants | `search`, `suggestions`, `category_browsing` |
| `simple_search.py` | `POST /rs/search` | `search` |
| `product_search.py` | `/rs/api/v1/products/search` | `search` |
| `product_api.py` | PDP, catalogue, scanner, batch | `pdp`, `search`, `category_browsing`, `recommendations` |
| `home_page.py` | home sections | `bestsellers`, `curated`, `flean_picks`, `pdp` |

## Configuration

All Search V2 settings are in `search_v2/config/settings.py` (`SearchV2Settings`),
overridable via environment variables. See `env.production.template` for the
production reference.

Required at runtime:

- `SEARCH_V2_ES_URL` (falls back to `ES_URL`)
- `SEARCH_V2_INDEX_NAME`
- `AWS_BEARER_TOKEN_BEDROCK` when `SEARCH_V2_EMBEDDING_BACKEND=bedrock`

## Local development

1. Start OpenSearch with the V2 index populated (see `search/search_v2/README.md`).
2. Copy `env.production.template` → `.env` and set Redis, OpenSearch, Bedrock token.
3. Run `python run.py` and hit `/rs/v1/search` or `/rs/search`.

Regression: `python postman_regression_runner.py --collection Flean_HomePage_Search_APIs.postman_collection.json --base-url http://localhost:8080`
