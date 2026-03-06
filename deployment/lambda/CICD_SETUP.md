# CI/CD Setup for Shopbot Lambda Deployment

## Required GitHub Secrets

Add these secrets to your repository (Settings → Secrets and variables → Actions):

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | AWS access key for shopbot-cicd-user |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for shopbot-cicd-user |
| `ES_URL` | Elasticsearch URL (from shopping-bot/es-url secret) |
| `ES_API_KEY` | Elasticsearch API key (from flean-services/shopbot secret) |

## Why These Secrets?

The CI workflow passes `ES_URL` and `ES_API_KEY` as Terraform variables to avoid requiring Secrets Manager permissions for the CI user. This allows deployment without granting the CI user access to read secrets.

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
