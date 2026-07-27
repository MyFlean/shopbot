# ShopBot (shopbot-main)

Flask backend for chat, search, and product APIs.

## Search

All product search runs on **Search V2** — hybrid lexical + semantic retrieval
against OpenSearch, with query embeddings from **Amazon Bedrock Titan Text
Embeddings V2**.

Start with [`search_v2/README.md`](search_v2/README.md) for architecture, the
request pipeline, runtime configuration, and local development.

The indexing pipeline (MongoDB → Bedrock Titan → OpenSearch) that produces the
Search V2 index lives in the separate `search` repository — see
`search/search_v2/README.md`.

## Local development

```bash
python run.py
```

Requires a `.env` (see `env.production.template` for the full variable
reference) with at minimum: Redis connection, OpenSearch connection
(`ES_URL` / `SEARCH_V2_ES_URL`), and `AWS_BEARER_TOKEN_BEDROCK` for both the
chat LLM and Search V2 semantic retrieval.

## Deployment

See `deployment/lambda/README.md` for the Lambda build/deploy process, and
`env.production.template` for the production environment variable reference.
