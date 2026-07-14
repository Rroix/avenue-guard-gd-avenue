#!/usr/bin/env bash
set -euo pipefail

python -m compileall -q main.py cogs utils tests
python -m ruff check main.py cogs utils tests --select E4,E7,E9,F,B
python -m pytest -q
python -m bandit -q -r main.py cogs utils -x tests -ll
python -m pip_audit -r requirements.txt --progress-spinner off
