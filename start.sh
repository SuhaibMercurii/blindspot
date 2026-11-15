#!/bin/sh
# Starts Blindspot on this computer and opens it in the browser. Safe to run again.
# First start: makes a private Python environment in .venv and installs fastapi, uvicorn, httpx.
# Options: PORT=8010 sh start.sh (another port), NO_OPEN=1 sh start.sh (do not open the browser).
cd "$(dirname "$0")" || exit 1
PORT="${PORT:-8000}"

pick_python() {
  for p in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  if PY=$(pick_python); then
    echo "First start: setting up with $PY (about a minute, needs internet)..."
    "$PY" -m venv .venv || exit 1
    .venv/bin/python -m pip install --quiet -r requirements.txt || {
      echo "The install failed. Check the internet connection, delete the .venv folder and run this again."; exit 1; }
  elif command -v uv >/dev/null 2>&1; then
    echo "First start: setting up with uv (about a minute, needs internet)..."
    uv venv --quiet --python 3.12 .venv && uv pip install --quiet --python .venv/bin/python -r requirements.txt || exit 1
  else
    echo "Blindspot needs Python 3.10 or newer, and this computer does not have it."
    echo "Install Python from https://www.python.org/downloads/ and run this again."
    exit 1
  fi
fi

if [ ! -f .env ]; then
  echo "No .env file here, so there are no keys: the saved runs replay with their real data,"
  echo "and a new run or a check uses made-up sample data. For live runs, copy .env.example to .env and add keys."
fi

URL="http://localhost:$PORT/?replay=pinned&fast=1"
echo "Blindspot is starting at http://localhost:$PORT   (press Ctrl+C in this window to stop it)"
if [ -z "$NO_OPEN" ]; then
  (sleep 3; open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null) &
fi
exec .venv/bin/python -m uvicorn app:app --port "$PORT"
