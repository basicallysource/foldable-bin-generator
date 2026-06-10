#!/usr/bin/env bash
# Deploy rev02 to Vercel and verify the deployment reproduces rev01's laser
# output (golden corpus over HTTP). Requires `npx vercel login` once first.
#
#   ./scripts/deploy_and_verify.sh          # preview deployment
#   ./scripts/deploy_and_verify.sh --prod   # production
#
# NOTE: preview URLs sit behind Vercel deployment protection (401 for the
# checker) unless protection is disabled in the project settings; use --prod
# (public) for the verification to run.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-/opt/homebrew/opt/python@3.11/libexec/bin/python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3

URL=$(npx vercel deploy --yes "$@" | tail -1)
echo
echo "deployed: $URL"
echo "verifying golden corpus against the deployment..."
"$PY" tests/check_deployed.py "$URL"
