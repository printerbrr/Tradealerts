#!/usr/bin/env sh
# Run after: pip install -r requirements.txt
# Use `python -m playwright` so PATH does not need a `playwright` executable (Railway/Railpack).
set -e
python -m playwright install chromium
python -m playwright install-deps chromium
