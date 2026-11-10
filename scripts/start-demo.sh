#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Each rehearsal gets a new directory; existing workspace records are untouched.
mkdir -p data/rehearsals
export LEDGERFLOW_DATA_DIR
LEDGERFLOW_DATA_DIR="$(mktemp -d "$PWD/data/rehearsals/demo-XXXXXXXX")"
export PORT="${PORT:-8001}"
# Repeatable PDF/text/structured demo; explicitly opt into vision when desired.
export ENABLE_OPENROUTER="${ENABLE_OPENROUTER:-false}"
printf 'Rehearsal data: %s\n' "$LEDGERFLOW_DATA_DIR"
exec ./scripts/start.sh
