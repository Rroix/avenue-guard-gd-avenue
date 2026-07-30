#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Python was not found. Set PYTHON_BIN to a Python 3.13 executable." >&2
    exit 1
  fi
fi

"$PYTHON_BIN" -m compileall -q main.py cogs utils scripts tests
"$PYTHON_BIN" -m ruff check main.py cogs utils scripts tests --select E4,E7,E9,F,B
"$PYTHON_BIN" -W error::DeprecationWarning -W error::ResourceWarning -m pytest -q
"$PYTHON_BIN" -m bandit -q -r main.py cogs utils scripts -x tests -ll
"$PYTHON_BIN" -m pip_audit -r requirements.txt --progress-spinner off
