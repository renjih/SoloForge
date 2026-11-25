#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

for script in scripts/check.sh scripts/check-clean.sh scripts/labels.sh; do
  bash -n "$script"
done
echo "Shell syntax checks passed"
python3 -m json.tool docs/arch-rules.json >/dev/null
echo "Architecture rules JSON is valid"
python3 -m json.tool templates/models.example.json >/dev/null
echo "Model example JSON is valid"

bash scripts/check-clean.sh
python3 scripts/check-links.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_check_links.py' -v
node scripts/test_arch_gate.js
node scripts/test_gate_step.js
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_ci_workflow.py' -v

python3 -m json.tool actions/dispatch/defaults/models.json >/dev/null
for script in adapters/codex/run adapters/codex/quota adapters/claude/run adapters/echo/run; do
  bash -n "$script"
done
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_dispatch_workflow.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s actions/dispatch -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s adapters -p 'test_*.py' -v

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/board -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_board_workflow.py' -v
bash -n tools/board/board-hook.sh

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_quota_patrol.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_runner_setup.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_template_workflow.py' -v
bash -n scripts/runner/setup.sh
(
  cd templates/wechat-minigame
  node scripts/check.mjs
  node --test 'test/**/*.test.mjs'
)
