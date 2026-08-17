#!/usr/bin/env bash

# Modifications Copyright (c) 2026 wslinnn
# This file has been modified from the upstream project
# https://github.com/CHEN010325/paddleocr-local (Apache-2.0).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif [[ -x ".venv-macos/bin/python" ]]; then
  PYTHON=".venv-macos/bin/python"
else
  PYTHON="python3"
fi

step() {
  printf "\n==> %s\n" "$1"
}

step "Checking Python syntax"
"$PYTHON" -m py_compile server.py ovisocr2_adapter.py rapidocr_adapter.py unlimited_ocr_adapter.py

step "Running server unit tests"
"$PYTHON" -m unittest discover -s tests -p "test_*.py" -v

step "Checking frontend JavaScript syntax"
node --check static/i18n.js
node --check static/app.js

# Frontend tests need jsdom from npm; skip (loudly) when npm is unavailable.
step "Running frontend tests"
if command -v npm >/dev/null 2>&1; then
  npm run test:frontend
else
  echo "npm not found — skipping frontend tests (install Node.js to run them)"
fi

step "Checking shell script syntax"
bash -n scripts/*.sh deploy.sh build.sh start-vlm.sh test-connection.sh

step "Running macOS Python selection tests"
bash tests/test_macos_python_selection.sh

step "Checking generated OpenAPI snapshot"
"$PYTHON" - <<'PY'
import importlib
import json
import os
import tempfile

os.environ["PANDOCR_TASK_DATA_DIR"] = tempfile.mkdtemp()
os.environ["PANDOCR_MODEL_CONTROL"] = "none"
os.environ["PANDOCR_API_TOKEN"] = ""

server = importlib.import_module("server")
current = server.app.openapi()
with open("webui-openapi.json", encoding="utf-8") as stream:
    saved = json.load(stream)

if current != saved:
    raise SystemExit("webui-openapi.json is stale. Regenerate it from server.app.openapi().")
PY

printf "\nAll local checks passed.\n"
