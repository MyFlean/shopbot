#!/bin/bash
set -e

echo "=== Building Lambda Deployment Package using Docker ==="

# Configuration
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="$PROJECT_ROOT/deployment/lambda"
PACKAGE_NAME="shopbot.zip"
DOCKER_IMAGE="lambda-builder-shopbot:latest"

cd "$PROJECT_ROOT"

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed or not in PATH"
    echo "Please install Docker Desktop or Docker Engine"
    exit 1
fi

# Build Docker image
echo "Building Docker image..."
cd "$BUILD_DIR"
docker build -f Dockerfile.build -t "$DOCKER_IMAGE" "$PROJECT_ROOT" || {
    echo "❌ Error: Docker build failed"
    exit 1
}

# Remove old package if exists
rm -f "$PROJECT_ROOT/$PACKAGE_NAME"

# Run Docker container to create package
# Override the Lambda entrypoint to run our build command
echo "Creating Lambda package in Docker container..."
docker run --rm \
    --entrypoint="" \
    -v "$PROJECT_ROOT:/output" \
    "$DOCKER_IMAGE" \
    sh -c "cp /build/shopbot.zip /output/$PACKAGE_NAME 2>/dev/null || (cd /build/package && zip -r /output/$PACKAGE_NAME . -q) && chmod 644 /output/$PACKAGE_NAME"

# Verify package was created
if [ ! -f "$PROJECT_ROOT/$PACKAGE_NAME" ]; then
    echo "❌ Error: Package file was not created!"
    exit 1
fi

# Display package info
PACKAGE_SIZE=$(du -h "$PROJECT_ROOT/$PACKAGE_NAME" | cut -f1)
PACKAGE_SIZE_BYTES=$(stat -f%z "$PROJECT_ROOT/$PACKAGE_NAME" 2>/dev/null || stat -c%s "$PROJECT_ROOT/$PACKAGE_NAME" 2>/dev/null)
MAX_SIZE=$((50 * 1024 * 1024))  # 50MB

echo ""
echo "✅ Package created successfully!"
echo "   Package: $PACKAGE_NAME"
echo "   Size: $PACKAGE_SIZE"
echo "   Location: $PROJECT_ROOT/$PACKAGE_NAME"

if [ "$PACKAGE_SIZE_BYTES" -gt "$MAX_SIZE" ]; then
    echo "⚠️  WARNING: Package size exceeds 50MB!"
    echo "   Consider using Lambda Layers for large dependencies"
fi

echo ""
echo "✅ Build complete! Package is ready for deployment."









