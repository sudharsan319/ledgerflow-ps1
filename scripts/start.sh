#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi
uv sync --frozen
if [[ ! -d frontend/node_modules ]]; then
  npm --prefix frontend ci
fi
npm --prefix frontend run build
printf '\nLedgerFlow is starting at http://127.0.0.1:%s\n' "${PORT:-8000}"
exec uv run uvicorn backend.app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
