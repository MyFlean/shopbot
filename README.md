# ShopBot (shopbot-main)

Flask backend for chat, search, and product APIs.

## Search

Product search runs two engines side by side, routed per-request (see
[`search_v2/README.md`](search_v2/README.md) §1 for the routing table):

- **Search V2** — hybrid lexical + semantic retrieval against OpenSearch,
  with query embeddings from **Amazon Bedrock Titan Text Embeddings V2**.
  Start with `search_v2/README.md` — it's the authoritative reference for
  architecture, the full request pipeline, runtime artifacts, S3 publishing,
  local dev, and known gaps.
- **Search V1** — the legacy `ElasticsearchProductsFetcher`
  (`shopping_bot/data_fetchers/es_products.py`), still serving most
  product-facing routes.

The indexing pipeline (MongoDB → Bedrock Titan → OpenSearch) that produces
what Search V2 reads lives in the separate `search` repository — see its own
`search_v2/README.md` for indexing, vocabulary/lexicon generation, and S3
publishing.

## Local development

```
python dev_search_cli.py   # interactive search REPL, exercises real production routing
```

Requires a `.env` (see `env.production.template` for the full variable
reference) with at minimum: Redis connection, OpenSearch connection
(`ES_URL`/`SEARCH_V2_ES_URL`), and `AWS_BEARER_TOKEN_BEDROCK` for both the
chat LLM and Search V2 semantic retrieval.

## Deployment

See `deployment/lambda/README.md` for the Lambda build/deploy/rollback
process, and `env.production.template` for the full production environment
variable reference.
