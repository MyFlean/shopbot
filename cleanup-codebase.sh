#!/bin/bash

# Cleanup script to remove AppRunner and unnecessary files
# Keeps only ECS and CI/CD related files

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Files to keep (ECS and CI/CD related)
KEEP_FILES=(
    "Dockerfile"
    "requirements.txt"
    "run.py"
    "README.md"
    "ecs-task-definition.json"
    "ecs-service.json"
    "deploy.sh"
    "test-cicd.sh"
    "setup-aws-credentials.sh"
    "setup-github-secrets.md"
    "CICD_SETUP.md"
    "github-secrets.txt"
    "terraform/"
    "shopping_bot/"
    ".github/"
)

# Files to remove (AppRunner and unnecessary)
REMOVE_FILES=(
    "apprunner.yaml"
    "apprunner.yaml.backup"
    "apprunner.yaml.bak"
    "cloudformation-ecs.yaml"
    "deploy-ecs.sh"
    "deploy-github.sh"
    "ECS_DEPLOYMENT_GUIDE.md"
    "GITHUB_DEPLOYMENT.md"
    "QUICK_START_GITHUB.md"
    "quick-deploy.sh"
    "SECRETS_MANAGER_GUIDE.md"
    "setup-secrets.sh"
    "logs.txt"
    "test_background.py"
    "test_flows.py"
    "test_payload.py"
    "test_two_phase_processing.py"
    "test-redis-connectivity.py"
    "testest.py"
    "Recommendation_Service.pdf"
    "env.production.template"
)

# Directories to remove
REMOVE_DIRS=(
    "myenv/"
    "myflean/"
    "venv/"
    "docs/"
)

# Function to remove files
remove_files() {
    log_info "Removing unnecessary files..."
    
    for file in "${REMOVE_FILES[@]}"; do
        if [ -f "$file" ]; then
            log_info "Removing: $file"
            rm -f "$file"
            log_success "Removed: $file"
        else
            log_warning "File not found: $file"
        fi
    done
}

# Function to remove directories
remove_directories() {
    log_info "Removing unnecessary directories..."
    
    for dir in "${REMOVE_DIRS[@]}"; do
        if [ -d "$dir" ]; then
            log_info "Removing directory: $dir"
            rm -rf "$dir"
            log_success "Removed directory: $dir"
        else
            log_warning "Directory not found: $dir"
        fi
    done
}

# Function to clean up Python cache files
cleanup_python_cache() {
    log_info "Cleaning up Python cache files..."
    
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -name "*.pyc" -delete 2>/dev/null || true
    find . -name "*.pyo" -delete 2>/dev/null || true
    
    log_success "Python cache files cleaned"
}

# Function to clean up Terraform state files (keep them for now)
cleanup_terraform() {
    log_info "Cleaning up Terraform temporary files..."
    
    # Remove .terraform directory if it exists
    if [ -d "terraform/.terraform" ]; then
        rm -rf "terraform/.terraform"
        log_success "Removed terraform/.terraform"
    fi
    
    # Keep terraform.tfstate files as they contain important state
    log_info "Keeping Terraform state files (terraform.tfstate*)"
}

# Function to create .gitignore
create_gitignore() {
    log_info "Creating/updating .gitignore..."
    
    cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
venv/
env/
ENV/
myenv/
myflean/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Logs
*.log
logs.txt

# Environment variables
.env
.env.local
.env.production

# Terraform
.terraform/
*.tfstate.backup
.terraform.lock.hcl

# AWS
.aws/

# Temporary files
*.tmp
*.temp
test_*.py
testest.py

# Documentation (keep only essential)
docs/
*.pdf

# Old deployment files
apprunner.yaml*
cloudformation-ecs.yaml
deploy-ecs.sh
deploy-github.sh
quick-deploy.sh
setup-secrets.sh

# Old guides
ECS_DEPLOYMENT_GUIDE.md
GITHUB_DEPLOYMENT.md
QUICK_START_GITHUB.md
SECRETS_MANAGER_GUIDE.md
EOF

    log_success "Created .gitignore"
}

# Function to show what will be kept
show_kept_files() {
    log_info "Files and directories that will be kept:"
    echo "=========================================="
    
    for item in "${KEEP_FILES[@]}"; do
        if [ -e "$item" ]; then
            if [ -d "$item" ]; then
                echo "📁 $item/"
            else
                echo "📄 $item"
            fi
        fi
    done
    
    echo ""
    log_info "Core application files:"
    echo "📄 Dockerfile"
    echo "📄 requirements.txt"
    echo "📄 run.py"
    echo "📁 shopping_bot/ (application code)"
    echo ""
    log_info "ECS deployment files:"
    echo "📄 ecs-task-definition.json"
    echo "📄 ecs-service.json"
    echo "📁 terraform/ (infrastructure)"
    echo ""
    log_info "CI/CD files:"
    echo "📄 deploy.sh"
    echo "📄 test-cicd.sh"
    echo "📄 setup-aws-credentials.sh"
    echo "📁 .github/ (GitHub Actions)"
    echo ""
    log_info "Documentation:"
    echo "📄 README.md"
    echo "📄 CICD_SETUP.md"
    echo "📄 setup-github-secrets.md"
    echo "📄 github-secrets.txt"
}

# Function to show what will be removed
show_removed_files() {
    log_info "Files and directories that will be removed:"
    echo "=============================================="
    
    echo "🗑️  AppRunner files:"
    echo "   - apprunner.yaml*"
    echo "   - cloudformation-ecs.yaml"
    echo "   - deploy-ecs.sh"
    echo "   - deploy-github.sh"
    echo "   - quick-deploy.sh"
    echo ""
    echo "🗑️  Old documentation:"
    echo "   - ECS_DEPLOYMENT_GUIDE.md"
    echo "   - GITHUB_DEPLOYMENT.md"
    echo "   - QUICK_START_GITHUB.md"
    echo "   - SECRETS_MANAGER_GUIDE.md"
    echo "   - setup-secrets.sh"
    echo ""
    echo "🗑️  Test files:"
    echo "   - test_*.py"
    echo "   - testest.py"
    echo "   - test-redis-connectivity.py"
    echo ""
    echo "🗑️  Virtual environments:"
    echo "   - myenv/"
    echo "   - myflean/"
    echo "   - venv/"
    echo ""
    echo ""
    echo "🗑️  Other files:"
    echo "   - logs.txt"
    echo "   - env.production.template"
}

# Function to create a summary of the cleanup
create_cleanup_summary() {
    log_info "Creating cleanup summary..."
    
    cat > CLEANUP_SUMMARY.md << 'EOF'
# Codebase Cleanup Summary

## What Was Removed

### AppRunner Related Files
- `apprunner.yaml*` - AppRunner configuration files
- `cloudformation-ecs.yaml` - CloudFormation template (using Terraform instead)
- `deploy-ecs.sh` - Old ECS deployment script
- `deploy-github.sh` - Old GitHub deployment script
- `quick-deploy.sh` - Quick deployment script

### Old Documentation
- `ECS_DEPLOYMENT_GUIDE.md` - Old ECS guide
- `GITHUB_DEPLOYMENT.md` - Old GitHub guide
- `QUICK_START_GITHUB.md` - Old quick start guide
- `SECRETS_MANAGER_GUIDE.md` - Old secrets guide
- `setup-secrets.sh` - Old secrets setup script

### Test Files
- `test_*.py` - Various test files
- `testest.py` - Test file
- `test-redis-connectivity.py` - Redis connectivity test

### Virtual Environments
- `myenv/` - Old virtual environment
- `myflean/` - Old virtual environment
- `venv/` - Virtual environment (should be recreated)

### Documentation
- `docs/` - Old documentation directory
- `Recommendation_Service.pdf` - PDF documentation

### Other Files
- `logs.txt` - Log file
- `env.production.template` - Environment template

## What Was Kept

### Core Application
- `Dockerfile` - Docker configuration
- `requirements.txt` - Python dependencies
- `run.py` - Application entry point
- `shopping_bot/` - Application source code
- `README.md` - Main documentation

### ECS Deployment
- `ecs-task-definition.json` - ECS task definition
- `ecs-service.json` - ECS service configuration
- `terraform/` - Infrastructure as Code

### CI/CD Pipeline
- `deploy.sh` - Main deployment script
- `test-cicd.sh` - CI/CD testing script
- `setup-aws-credentials.sh` - AWS credentials setup
- `.github/` - GitHub Actions workflows

### Documentation
- `CICD_SETUP.md` - CI/CD setup guide
- `setup-github-secrets.md` - GitHub secrets guide
- `github-secrets.txt` - GitHub secrets

## Next Steps

1. **Recreate Virtual Environment** (if needed):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Test Deployment**:
   ```bash
   ./deploy.sh deploy
   ```

3. **Test CI/CD**:
   ```bash
   ./test-cicd.sh
   ```

4. **Push to GitHub**:
   ```bash
   git add .
   git commit -m "Clean up codebase - remove AppRunner files"
   git push origin main
   ```

## Benefits

- ✅ Cleaner codebase focused on ECS deployment
- ✅ Removed outdated AppRunner configurations
- ✅ Streamlined CI/CD pipeline
- ✅ Better organization of files
- ✅ Reduced repository size
EOF

    log_success "Created CLEANUP_SUMMARY.md"
}

# Main cleanup function
cleanup_codebase() {
    log_info "🧹 Starting codebase cleanup..."
    echo ""
    
    # Show what will be kept and removed
    show_kept_files
    echo ""
    show_removed_files
    echo ""
    
    # Ask for confirmation
    read -p "Do you want to proceed with the cleanup? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Cleanup cancelled by user"
        exit 0
    fi
    
    echo ""
    log_info "Proceeding with cleanup..."
    echo ""
    
    # Perform cleanup
    remove_files
    echo ""
    remove_directories
    echo ""
    cleanup_python_cache
    echo ""
    cleanup_terraform
    echo ""
    create_gitignore
    echo ""
    create_cleanup_summary
    echo ""
    
    log_success "🎉 Codebase cleanup completed!"
    echo ""
    log_info "Summary:"
    echo "- Removed AppRunner and unnecessary files"
    echo "- Kept ECS and CI/CD related files"
    echo "- Created .gitignore"
    echo "- Created cleanup summary"
    echo ""
    log_info "Next steps:"
    echo "1. Review the changes: git status"
    echo "2. Commit the cleanup: git add . && git commit -m 'Clean up codebase'"
    echo "3. Push to GitHub: git push origin main"
}

# Show usage
usage() {
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  cleanup    Remove AppRunner and unnecessary files"
    echo "  preview    Show what will be kept and removed"
    echo "  help       Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 cleanup    # Remove unnecessary files"
    echo "  $0 preview    # Preview what will be changed"
}

# Preview mode
preview_cleanup() {
    log_info "📋 Preview of codebase cleanup..."
    echo ""
    show_kept_files
    echo ""
    show_removed_files
    echo ""
    log_info "Run '$0 cleanup' to proceed with the cleanup"
}

# Parse command line arguments
case "${1:-preview}" in
    cleanup)
        cleanup_codebase
        ;;
    preview)
        preview_cleanup
        ;;
    help|--help|-h)
        usage
        exit 0
        ;;
    *)
        echo "Unknown command: $1"
        usage
        exit 1
        ;;
esac
