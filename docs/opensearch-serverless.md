# Shopbot search: Amazon OpenSearch Serverless (AOSS)

Production search uses **Amazon OpenSearch Serverless** with **AWS SigV4** (IAM). The app does **not** use `ELASTIC_API_KEY` for that path.

## Required environment variables

| Variable | Value |
|----------|--------|
| `ES_URL` | HTTPS collection endpoint: `https://<collection-id>.<region>.aoss.amazonaws.com` (no trailing path; index is appended in code). |
| `ELASTIC_INDEX` | e.g. `products_v3` (must match the index on the collection). |
| `AOSS_ENABLED` | `true` |
| `ES_USE_IAM` | `true` |
| `SEARCH_AWS_REGION` | Same region as the collection (e.g. `ap-south-1`). Falls back to `AWS_REGION` / `AWS_DEFAULT_REGION` if unset. |

Do **not** set `ES_URL` to `*.elastic.cloud` when IAM/AOSS is enabled; SigV4 service `aoss` only works against Serverless endpoints.

## Where the endpoint comes from

Infrastructure for the `flean-products-v3` collection lives in the **`flean/search`** repo (`opensearch_products_v3.tf`). After apply:

```bash
cd /path/to/flean/search
terraform output -raw opensearch_products_v3_endpoint
```

Use the printed value as `ES_URL` (ensure it starts with `https://`).

## AWS Secrets Manager (Lambda / ECS)

There are two relevant secrets; do not point either at Elastic Cloud once you are on AOSS+IAM.

- **`shopping-bot/es-url` (string):** If `var.es_url` is empty, `deployment/lambda/main.tf` sets Lambda `ES_URL` from this secret. Store **only** the AOSS HTTPS URL string.
- **`flean-services/shopbot` (JSON):** The handler loads this for API keys, Redis mapping, etc. The JSON may still contain an `ES_URL` key from older setups. **The Lambda `ES_URL` is set first** (Terraform or `shopping-bot/es-url`); the handler **does not overwrite** it when the value is already a Serverless URL (`*.aoss.amazonaws.com`), so a stale `*.elastic.cloud` value in the JSON cannot override AOSS. You can still remove or align `ES_URL` in the JSON to avoid confusion in the AWS console.
- **ECS:** `ecs-task-definition.json` injects `ES_URL` from a Secrets Manager ARN (`shopping-bot/es-url-*`). Use the same AOSS URL.

### Update the secret (CLI example)

Replace `<SECRET_ID>` with `shopping-bot/es-url` or your ARN’s secret id, and use your real endpoint:

```bash
aws secretsmanager put-secret-value \
  --region ap-south-1 \
  --secret-id shopping-bot/es-url \
  --secret-string "https://YOUR_COLLECTION_ID.ap-south-1.aoss.amazonaws.com"
```

Then **update Lambda** (new task revision for ECS, or redeploy Lambda / wait for next deploy) so processes pick up the new value.

## IAM

The Lambda execution role should allow OpenSearch Serverless API access (see `lambda_aoss` in `deployment/lambda/main.tf`: `aoss:APIAccessAll`, etc.). The collection **data access policy** must include the Lambda role ARN (and ECS task role if ECS runs Shopbot). This is provisioned in `flean/search` for `shopbot-service-lambda-role` and `flean-services-ecs-task-role`.

## GitHub Actions

The workflow (`.github/workflows/deploy-lambda.yml`) deploys Lambda code with **AWS CLI** (`deploy-lambda-aws-cli.sh`), not Terraform.

- Repository secret **`ES_URL`** (optional): if set, the workflow runs **`update-lambda-env-aoss.sh`** so the Lambda environment includes the AOSS endpoint and flags. If unset, only the zip is updated; existing env vars stay as in the console.
- **`ES_API_KEY`** is not used by the AWS CLI deploy path.

## Verify

After deploy, CloudWatch logs for the fetcher should show `IAM_AUTH: ENABLED` and `BASE_URL` host containing `aoss.amazonaws.com`. A wrong Elastic Cloud URL will fail fast with a clear `RuntimeError` from `ElasticsearchProductsFetcher`.
