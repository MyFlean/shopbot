# Security Guide - Files NOT to Commit to GitHub


## 🚨 **CRITICAL: Never Commit These Files to GitHub**

### **Environment Files**
- `.env_prod` - Contains production environment variables including API keys
- `.env` - Local environment variables
- `.env.local` - Local development environment
- `.env.production` - Production environment variables

### **Secrets and Credentials**
- `github-secrets.txt` - Contains AWS access keys and secrets
- `setup-aws-credentials.sh` - AWS credential setup script
- `setup-github-secrets.md` - GitHub secrets configuration guide

### **API Keys and Tokens**
- Any file containing `ANTHROPIC_API_KEY`
- Any file containing `AWS_ACCESS_KEY_ID`
- Any file containing `AWS_SECRET_ACCESS_KEY`
- Any file containing `ELASTIC_API_KEY`
- Any file containing `SECRET_KEY`

### **Private Keys**
- `FLOW_PRIVATE_KEY` - WhatsApp Flow private key
- Any `.pem`, `.key`, `.p12`, `.pfx` files
- SSH private keys (`id_rsa`, `id_ed25519`, etc.)

## ✅ **Files Safe to Commit**

### **Configuration Templates**
- `env.production.template` - Template for environment variables
- `requirements.txt` - Python dependencies
- `Dockerfile` - Container configuration
- `terraform/` - Infrastructure as Code (no secrets)

### **Application Code**
- `shopping_bot/` - Application source code
- `run.py` - Application entry point
- `*.py` files (without hardcoded secrets)

### **Documentation**
- `README.md` - Project documentation
- `CICD_SETUP.md` - CI/CD setup guide
- `SECURITY_GUIDE.md` - This security guide

## 🔒 **Current .gitignore Protection**

The following patterns are already protected in `.gitignore`:

```gitignore
# Environment variables
.env
.env.local
.env.production
.env_prod
.env_production

# Secrets and Credentials
github-secrets.txt
setup-aws-credentials.sh
setup-github-secrets.md
*secret*.txt
*secret*.md
*credential*.txt
*credential*.sh
*credential*.md
*.key
*.pem
*.p12
*.pfx
id_rsa
id_ed25519

# Exception: Allow security check script
!check-secrets.sh
```

## 🛡️ **Security Best Practices**

### **1. Use Environment Variables**
```python
# ✅ Good - Use environment variables
import os
api_key = os.getenv('ANTHROPIC_API_KEY')

# ❌ Bad - Hardcoded secrets
api_key = "sk-ant-api03-EXAMPLE-KEY-DO-NOT-USE-REPLACE-WITH-YOUR-ACTUAL-KEY"
```

### **2. Use AWS Secrets Manager**
```python
# ✅ Good - Use AWS Secrets Manager
import boto3
secrets_client = boto3.client('secretsmanager')
secret = secrets_client.get_secret_value(SecretId='shopbot-secrets')
```

### **3. Use GitHub Secrets for CI/CD**
- Store sensitive values in GitHub repository secrets
- Reference them in workflows as `${{ secrets.SECRET_NAME }}`

### **4. Use Terraform Variables**
```hcl
# ✅ Good - Use variables
variable "redis_host" {
  description = "Redis host"
  type        = string
  sensitive   = true
}
```

## 🔍 **How to Check for Secrets**

### **Before Committing**
```bash
# Check for common secret patterns
grep -r "sk-ant-api" . --exclude-dir=.git
grep -r "AKIA" . --exclude-dir=.git
grep -r "password" . --exclude-dir=.git
grep -r "secret" . --exclude-dir=.git
```

### **Use Git Hooks**
Create `.git/hooks/pre-commit`:
```bash
#!/bin/bash
# Check for secrets before committing
if grep -r "sk-ant-api\|AKIA\|password.*=" . --exclude-dir=.git; then
    echo "❌ SECRETS DETECTED! Do not commit sensitive information."
    exit 1
fi
echo "✅ No secrets detected"
```

## 🚨 **If You Accidentally Commit Secrets**

### **1. Immediate Actions**
```bash
# Remove from git history
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch .env_prod github-secrets.txt' \
  --prune-empty --tag-name-filter cat -- --all

# Force push to remove from remote
git push origin --force --all
```

### **2. Rotate Credentials**
- Change all API keys immediately
- Generate new AWS access keys
- Update all environments with new credentials

### **3. Use GitGuardian or Similar**
- Set up secret scanning
- Monitor for exposed credentials
- Get alerts for potential leaks

## 📋 **Pre-Commit Checklist**

- [ ] No `.env*` files in commit
- [ ] No hardcoded API keys
- [ ] No AWS credentials
- [ ] No private keys
- [ ] No passwords or secrets
- [ ] All sensitive data uses environment variables
- [ ] GitHub secrets configured for CI/CD

## 🔧 **Environment Setup**

### **Local Development**
```bash
# Copy template and add your values
cp env.production.template .env_prod
# Edit .env_prod with your actual values (DO NOT COMMIT)
```

### **Production Deployment**
- Use AWS Secrets Manager
- Use GitHub repository secrets
- Use environment variables in ECS task definition

## 📞 **Emergency Contacts**

If you suspect credentials have been compromised:
1. **Immediately rotate all affected credentials**
2. **Check AWS CloudTrail for unauthorized access**
3. **Review GitHub repository access logs**
4. **Update all environments with new credentials**

---

**Remember: Security is everyone's responsibility. When in doubt, don't commit it!**
