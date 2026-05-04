#!/bin/bash
# Local build for Lambda (no Docker) - uses pip with Linux target
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PACKAGE_DIR="$PROJECT_ROOT/build/lambda-package"
ZIP_FILE="$PROJECT_ROOT/shopbot.zip"

cd "$PROJECT_ROOT"
rm -rf "$PACKAGE_DIR" "$ZIP_FILE"
mkdir -p "$PACKAGE_DIR"

echo "Installing dependencies..."
pip install --target "$PACKAGE_DIR" --platform manylinux2014_x86_64 --python-version 3.12 -r requirements.txt --upgrade 2>/dev/null || \
pip install --target "$PACKAGE_DIR" -r requirements.txt --upgrade

echo "Copying application code..."
cp lambda_handler.py run.py "$PACKAGE_DIR/"
cp -r shopping_bot "$PACKAGE_DIR/"

echo "Cleaning up..."
find "$PACKAGE_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DIR" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DIR" -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
find "$PACKAGE_DIR" -name "*.pyc" -delete 2>/dev/null || true

echo "Creating zip..."
cd "$PACKAGE_DIR"
zip -r "$ZIP_FILE" . -q
cd "$PROJECT_ROOT"
rm -rf "$PACKAGE_DIR"

echo "✅ Package created: $ZIP_FILE ($(du -h "$ZIP_FILE" | cut -f1))"
