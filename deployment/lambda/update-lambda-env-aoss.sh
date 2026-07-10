#!/usr/bin/env bash
# Backward-compatible wrapper: delegates to update-lambda-env.sh (manifest merge + optional ES_URL).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/update-lambda-env.sh"
