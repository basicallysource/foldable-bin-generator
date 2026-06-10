#!/usr/bin/env bash
# Local API for `npm run dev` — the same Flask app Vercel runs as a Python
# function. Uses $PYTHON if set, else the homebrew 3.11 this project standardises on.
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-/opt/homebrew/opt/python@3.11/libexec/bin/python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
exec "$PY" api/index.py
