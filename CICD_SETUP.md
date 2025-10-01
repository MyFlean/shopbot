# CI/CD Setup Guide

This guide explains how to set up and use the CI/CD pipeline for the ShopBot application.

## 🚀 Overview

The CI/CD pipeline automatically builds, tests, and deploys your application to AWS ECS Fargate whenever you push changes to the main branch.

## 📋 Prerequisites

- GitHub repository with the ShopBot code
- AWS account with appropriate permissions
- Docker installed locally (for testing)
- AWS CLI configured

## 🔧 Setup Steps

### 1. Configure GitHub Secrets

Add these secrets to your GitHub repository:

1. Go to your GitHub repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add these secrets:

   **AWS_ACCESS_KEY_ID**
   - Value: Your AWS access key ID

   **AWS_SECRET_ACCESS_KEY**
   - Value: Your AWS secret access key

### 2. Verify AWS Resources

Ensure these AWS resources exist:
- ECR repository: `shopbot`
- ECS cluster: `shopbot-cluster`
- ECS service: `shopbot-service`
- Application Load Balancer
- ElastiCache Redis cluster

### 3. Test the Pipeline

1. Push a change to the main branch
2. Check the **Actions** tab in GitHub
3. Watch the "Deploy to ECS" workflow run

## 📁 Pipeline Files

### `.github/workflows/deploy.yml`
Main GitHub Actions workflow that:
- Builds Docker image
- Pushes to ECR
- Updates ECS service

### `deploy.sh`
Local deployment script with commands:
- `deploy` - Deploy to ECS
- `status` - Check deployment status
- `health` - Check application health
- `rollback` - Rollback to previous version

### `test-cicd.sh`
Script to test CI/CD setup locally

## 🔄 Workflow Process

1. **Trigger**: Push to main branch
2. **Build**: Create Docker image
3. **Push**: Upload image to ECR
4. **Deploy**: Update ECS service
5. **Verify**: Check deployment health

## 🧪 Testing

### Local Testing
```bash
# Test CI/CD setup
./test-cicd.sh

# Test deployment
./deploy.sh deploy

# Check health
./deploy.sh health
```

### GitHub Actions Testing
1. Make a small change to your code
2. Commit and push to main branch
3. Check GitHub Actions tab
4. Verify deployment in AWS console

## 🚨 Troubleshooting

### Common Issues

1. **Authentication Errors**
   - Verify GitHub secrets are correct
   - Check AWS credentials permissions

2. **Build Failures**
   - Check Dockerfile syntax
   - Verify all dependencies in requirements.txt

3. **Deployment Failures**
   - Check ECS service logs
   - Verify ALB health checks
   - Check Redis connectivity

### Debug Commands

```bash
# Check AWS resources
aws ecs describe-services --cluster shopbot-cluster --services shopbot-service

# Check ECR images
aws ecr describe-images --repository-name shopbot

# Check ALB health
curl http://your-alb-url/health
```

## 📊 Monitoring

### GitHub Actions
- View workflow runs in Actions tab
- Check logs for each step
- Monitor deployment status

### AWS Console
- ECS service events
- CloudWatch logs
- ALB target health

## 🔒 Security Best Practices

1. **Use Environment Secrets**
   - Store sensitive data in GitHub secrets
   - Never commit credentials to code

2. **Least Privilege Access**
   - Create dedicated IAM user for CI/CD
   - Grant only necessary permissions

3. **Regular Updates**
   - Keep dependencies updated
   - Rotate access keys regularly

## 📈 Advanced Features

### Environment Promotion
- Deploy to staging first
- Manual approval for production
- Automated rollback on failure

### Blue/Green Deployments
- Zero-downtime deployments
- Instant rollback capability
- Traffic shifting

### Monitoring Integration
- CloudWatch alarms
- Slack notifications
- Performance metrics

## 🆘 Support

If you encounter issues:

1. Check GitHub Actions logs
2. Review AWS CloudWatch logs
3. Verify all prerequisites
4. Test locally first

## 📚 Additional Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [AWS ECS Documentation](https://docs.aws.amazon.com/ecs/)
- [Docker Documentation](https://docs.docker.com/)
