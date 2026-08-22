#!/usr/bin/env bash
# Run the backend and the frontend dev server together.
#   ./scripts/dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies..."
  npm --prefix frontend install
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "Backend  → http://127.0.0.1:8000"
echo "Frontend → http://127.0.0.1:5173"
echo

"$PYTHON" run.py --reload &
npm --prefix frontend run dev &
wait
