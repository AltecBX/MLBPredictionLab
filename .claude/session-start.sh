#!/usr/bin/env bash
# Prepare a fresh session so tests and linters can run immediately.
# Idempotent and quiet on success; never fails the session.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 0

if [ ! -d .venv ]; then
  python3 -m venv .venv >/dev/null 2>&1 || exit 0
fi
.venv/bin/pip install --quiet --upgrade pip >/dev/null 2>&1
.venv/bin/pip install --quiet -e "backend[dev]" >/dev/null 2>&1

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

if [ ! -d frontend/node_modules ]; then
  (cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --no-audit --no-fund >/dev/null 2>&1)
fi

echo "Environment ready: .venv + frontend/node_modules. Run 'make help' for tasks."
exit 0
