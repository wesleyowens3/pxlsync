#!/usr/bin/env bash
# Build pxlsync.app on a Mac. Output: dist/pxlsync.app
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
pyinstaller --noconfirm pxlsync.spec
echo "Built dist/pxlsync.app"
