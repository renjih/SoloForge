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
