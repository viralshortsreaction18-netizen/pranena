#!/usr/bin/env bash
# Start the Pranena OCR server.
#
#   ./run.sh              # http://localhost:8000
#   ./run.sh --port 9000  # a different port
set -euo pipefail

cd "$(dirname "$0")"

PORT=8000
HOST=0.0.0.0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    -h|--help)
      echo "usage: ./run.sh [--host HOST] [--port PORT]"
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating the virtual environment…"
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt
fi

echo "Pranena → http://${HOST}:${PORT}"
exec .venv/bin/python -m uvicorn backend.main:app --host "$HOST" --port "$PORT" "$@"
