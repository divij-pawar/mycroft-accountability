#!/bin/bash
# Start the verification-layer web UI.
#
# Resolves its own location instead of assuming a working directory, so it works
# from anywhere. Activates env/ only if that virtualenv exists — otherwise it uses
# whatever python/uvicorn is already on PATH, which is what a system-python or
# conda setup needs.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ -f env/bin/activate ]; then
  # shellcheck disable=SC1091
  source env/bin/activate
elif [ -f env/Scripts/activate ]; then
  # shellcheck disable=SC1091
  source env/Scripts/activate
fi

# React UI (served at / and /app): build it if it has never been built. The classic
# UI that needed no Node was archived on 2026-09-27 (archive/web-static-legacy/), so
# without a build "/" explains how to make one; the API itself runs either way.
if [ ! -d web/frontend/dist ]; then
  if command -v npm >/dev/null 2>&1; then
    (cd web/frontend && npm ci && npm run build)
  else
    echo "note: npm not found — the UI can't be built; the API still runs (see /docs)." >&2
  fi
fi

exec python -m uvicorn web.server:app --reload --port "${PORT:-8000}"
