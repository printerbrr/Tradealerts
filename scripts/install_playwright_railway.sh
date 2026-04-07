#!/usr/bin/env sh
# Run after: pip install -r requirements.txt
# Use `python -m playwright` so PATH does not need a `playwright` executable (Railway/Railpack).
# PLAYWRIGHT_BROWSERS_PATH=0 installs browsers inside site-packages so the runtime image
# includes them (default cache under /root/.cache is dropped on Railway).
set -e
export PLAYWRIGHT_BROWSERS_PATH=0
python -m playwright install chromium
python -m playwright install-deps chromium
