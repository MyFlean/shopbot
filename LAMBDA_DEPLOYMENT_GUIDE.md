# Lambda Deployment Process & Troubleshooting

## 📋 Deployment Process

### Manual Deployment (using `deploy.sh` script)

```bash
cd deployment/lambda
./deploy.sh
```

**Steps:**
1. **Prerequisites Check**: Verifies Docker, Terraform, and AWS CLI are installed
2. **Build Lambda Package**: Runs `build-docker.sh` to create `shopbot.zip` using Docker
3. **Terraform Init**: Initializes Terraform (if needed)
4. **Terraform Plan**: Creates deployment plan
5. **User Confirmation**: Asks for "yes" to proceed
6. **Terraform Apply**: Deploys infrastructure and Lambda function
7. **Output**: Shows API Gateway URL and Lambda function name

### Automated Deployment (GitHub Actions)

The `.github/workflows/deploy-lambda.yml` workflow automatically:
1. Builds Lambda package using Docker (`build-docker.sh`)
2. Initializes Terraform
3. Validates Terraform config
4. Runs `terraform plan`
5. Runs `terraform apply -auto-approve`
6. Verifies deployment
7. Tests health endpoint

**Triggers:**
- Push to `main`/`master` branch
- Changes to: `shopping_bot/**`, `lambda_handler.py`, `requirements.txt`, `deployment/lambda/**`

---

## ❌ Why Lambda Deployment is Failing

Based on the GitHub Actions logs, **Terraform Apply** step is consistently failing. Here are the likely causes:

### 1. **Package Path Issue** (Most Likely)
```terraform
filename = "${path.module}/../../shopbot.zip"
```
- The package must exist at `deployment/lambda/../../shopbot.zip` (project root)
- If Docker build fails or package isn't created, Terraform will fail

### 2. **Terraform State Issues**
- State file might be out of sync
- Lock conflicts if multiple deployments run simultaneously
- Missing state backend configuration

### 3. **AWS Permissions**
- GitHub Actions AWS credentials might not have:
  - `lambda:CreateFunction`
  - `lambda:UpdateFunctionCode`
  - `apigateway:*`
  - `iam:PassRole`
  - `secretsmanager:GetSecretValue`

### 4. **Secrets Manager Access**
```terraform
data "aws_secretsmanager_secret" "es_url" {
  name = "shopping-bot/es-url"
}
```
- If the secret doesn't exist or credentials can't access it, Terraform will fail

### 5. **VPC/Subnet Configuration**
```terraform
vpc_config {
  subnet_ids = var.private_subnet_ids
  security_group_ids = [aws_security_group.lambda.id]
}
```
- If `private_subnet_ids` are invalid or don't exist, deployment fails

### 6. **Package Size**
- Lambda has a 50MB limit for direct upload
- If `shopbot.zip` exceeds this, it needs to be uploaded to S3 first

---

## 🔍 How to Debug

### Check GitHub Actions Logs
1. Go to: https://github.com/MyFlean/shopbot/actions
2. Click on the failed "Deploy Lambda Function" run
3. Expand "Terraform Apply" step
4. Look for error messages

### Common Error Messages:

**"Error: Error creating Lambda Function"**
- Check AWS permissions
- Verify package exists and is valid
- Check Lambda service quotas

**"Error: Error reading Secrets Manager secret"**
- Verify secret exists: `aws secretsmanager describe-secret --secret-id shopping-bot/es-url`
- Check IAM permissions for Secrets Manager

**"Error: InvalidParameterValueException"**
- Package might be corrupted
- Runtime mismatch (should be `python3.12`)
- Handler path incorrect

**"Error: ResourceConflictException"**
- Function already exists with different configuration
- Need to update instead of create

---

## ✅ Quick Fixes

### Fix 1: Verify Package Build
```bash
cd deployment/lambda
./build-docker.sh
ls -lh ../../shopbot.zip  # Should exist and be < 50MB
```

### Fix 2: Check Terraform State
```bash
cd deployment/lambda
terraform init
terraform plan  # See what changes are planned
```

### Fix 3: Verify AWS Credentials
```bash
aws sts get-caller-identity  # Should show your AWS account
aws lambda list-functions | grep shopbot  # Check if function exists
```

### Fix 4: Check Secrets
```bash
aws secretsmanager describe-secret --secret-id shopping-bot/es-url
aws secretsmanager get-secret-value --secret-id <your-secret-arn>
```

---

## 🚀 Recommended Solution

**Since ECS deployment is working**, you have two options:

### Option A: Fix Lambda Deployment
1. Check the actual error in GitHub Actions logs
2. Fix the specific issue (permissions, secrets, package, etc.)
3. Re-run the workflow

### Option B: Use ECS Only (Current Working Solution)
- ECS deployment is working perfectly
- All APIs are accessible via ALB
- Lambda might be redundant if you're using ECS

**Recommendation**: Check if Lambda is actually needed, or if you can use ECS exclusively.

---

## 📝 Key Files

- **Deployment Script**: `deployment/lambda/deploy.sh`
- **Build Script**: `deployment/lambda/build-docker.sh`
- **Terraform Config**: `deployment/lambda/main.tf`
- **GitHub Workflow**: `.github/workflows/deploy-lambda.yml`
- **Lambda Handler**: `lambda_handler.py`

---

## 🔗 Related Documentation

- [AWS Lambda Deployment Package](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html)
- [Terraform AWS Lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function)
- [GitHub Actions AWS Setup](https://github.com/aws-actions/configure-aws-credentials)

