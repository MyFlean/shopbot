#!/bin/bash

# Security Check Script
# This script checks for potential secrets in the repository

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

# Check for common secret patterns
check_secrets() {
    log_info "🔍 Checking for potential secrets..."
    
    local secrets_found=false
    
    # Check for Anthropic API keys
    if grep -r "sk-ant-api" . --exclude-dir=.git --exclude="check-secrets.sh" --exclude="SECURITY_GUIDE.md" >/dev/null 2>&1; then
        log_error "❌ Anthropic API keys found!"
        secrets_found=true
    fi
    
    # Check for AWS access keys
    if grep -r "AKIA" . --exclude-dir=.git --exclude="check-secrets.sh" --exclude="SECURITY_GUIDE.md" >/dev/null 2>&1; then
        log_error "❌ AWS access keys found!"
        secrets_found=true
    fi
    
    # Check for hardcoded passwords
    if grep -r "password.*=" . --exclude-dir=.git --exclude="check-secrets.sh" --exclude="SECURITY_GUIDE.md" >/dev/null 2>&1; then
        log_warning "⚠️  Potential hardcoded passwords found!"
        secrets_found=true
    fi
    
    # Check for secret keys
    if grep -r "SECRET_KEY.*=" . --exclude-dir=.git --exclude="check-secrets.sh" --exclude="SECURITY_GUIDE.md" >/dev/null 2>&1; then
        log_warning "⚠️  Potential secret keys found!"
        secrets_found=true
    fi
    
    # Check for environment files
    if ls .env* >/dev/null 2>&1; then
        log_warning "⚠️  Environment files found:"
        ls -la .env* 2>/dev/null || true
        secrets_found=true
    fi
    
    if [ "$secrets_found" = true ]; then
        log_error "🚨 SECRETS DETECTED! Do not commit these files."
        echo ""
        log_info "Files to check:"
        echo "  - .env* files"
        echo "  - github-secrets.txt"
        echo "  - setup-aws-credentials.sh"
        echo "  - Any files with hardcoded API keys"
        echo ""
        log_info "Run 'git status' to see what's staged for commit"
        return 1
    else
        log_success "✅ No secrets detected!"
        return 0
    fi
}

# Check git status for sensitive files
check_git_status() {
    log_info "📋 Checking git status for sensitive files..."
    
    local sensitive_files=$(git status --porcelain | grep -E "\.(env|key|pem|p12|pfx)$|secret|credential" || true)
    
    if [ -n "$sensitive_files" ]; then
        log_error "❌ Sensitive files detected in git status:"
        echo "$sensitive_files"
        return 1
    else
        log_success "✅ No sensitive files in git status"
        return 0
    fi
}

# Check .gitignore
check_gitignore() {
    log_info "🔒 Checking .gitignore protection..."
    
    local protected_patterns=(
        ".env"
        "github-secrets.txt"
        "setup-aws-credentials.sh"
        "*secret*"
        "*credential*"
        "*key*"
    )
    
    local missing_patterns=()
    
    for pattern in "${protected_patterns[@]}"; do
        if ! grep -q "$pattern" .gitignore; then
            missing_patterns+=("$pattern")
        fi
    done
    
    if [ ${#missing_patterns[@]} -eq 0 ]; then
        log_success "✅ .gitignore properly configured"
        return 0
    else
        log_warning "⚠️  Missing patterns in .gitignore:"
        for pattern in "${missing_patterns[@]}"; do
            echo "  - $pattern"
        done
        return 1
    fi
}

# Main function
main() {
    echo "🛡️  SECURITY CHECK FOR SHOPBOT REPOSITORY"
    echo "=========================================="
    echo ""
    
    local exit_code=0
    
    # Run all checks
    check_secrets || exit_code=1
    echo ""
    
    check_git_status || exit_code=1
    echo ""
    
    check_gitignore || exit_code=1
    echo ""
    
    if [ $exit_code -eq 0 ]; then
        log_success "🎉 All security checks passed!"
        log_info "Safe to commit your changes."
    else
        log_error "🚨 Security issues detected!"
        log_info "Please fix the issues above before committing."
        log_info "See SECURITY_GUIDE.md for more information."
    fi
    
    return $exit_code
}

# Run main function
main "$@"
