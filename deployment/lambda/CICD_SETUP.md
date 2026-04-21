# CI/CD Setup for Shopbot Lambda Deployment

## Required GitHub Secrets

Add these secrets to your repository (Settings → Secrets and variables → Actions):

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | AWS access key for shopbot-cicd-user |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for shopbot-cicd-user |
| `SHOPBOT_SECRETS_ARN` | Full ARN of the flean-services/shopbot secret (for Lambda IAM policy) |
| `ES_URL` | **OpenSearch Serverless** collection HTTPS URL (`https://<id>.<region>.aoss.amazonaws.com`). Must not be Elastic Cloud (`*.elastic.cloud`) when Lambda uses IAM/AOSS. See [docs/opensearch-serverless.md](../../docs/opensearch-serverless.md). |
| `ES_API_KEY` | Not used by the AWS CLI deploy workflow. Optional legacy secret. |

## Why These Secrets?

- **Deploy:** GitHub Actions runs `deployment/lambda/deploy-lambda-aws-cli.sh` (no Terraform). It uploads `shopbot.zip` via `aws lambda update-function-code`.
- **Optional env sync:** If `ES_URL` is set, the workflow runs `update-lambda-env-aoss.sh` to merge AOSS variables into the Lambda environment. Omit `ES_URL` to leave the function’s existing environment unchanged.
- `SHOPBOT_SECRETS_ARN` is used for the Lambda IAM policy so the function can read secrets at runtime.

## Alternative: IAM Permissions

If you prefer the CI user to read from Secrets Manager (no GitHub secrets for ES values), attach the policy in `iam-policy-cicd.json` to the `shopbot-cicd-user`:

```bash
aws iam put-user-policy \
  --user-name shopbot-cicd-user \
  --policy-name ShopbotCICDPolicy \
  --policy-document file://deployment/lambda/iam-policy-cicd.json
```

Then remove `TF_VAR_es_url` and `TF_VAR_es_api_key` from the workflow and add `enable_custom_domain: "true"` to terraform.tfvars for full custom domain support.

## Custom Domain (api-rs.flean.ai)

By default, CI sets `enable_custom_domain=false` to avoid Route53 permissions. The API will be available at the default API Gateway URL (e.g. `https://xxx.execute-api.ap-south-1.amazonaws.com`).

To enable the custom domain in CI, the shopbot-cicd-user needs Route53 permissions. Use the `iam-policy-cicd.json` which includes Route53 access, then set `TF_VAR_enable_custom_domain: "true"` in the workflow.
