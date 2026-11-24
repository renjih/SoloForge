#!/usr/bin/env python3
"""Update the embedded architecture checker in the reusable workflows."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github/workflows"
BLOCK = re.compile(r"^( +)// >>> arch_gate\.js\n.*?^\1// <<< arch_gate\.js$", re.M | re.S)


def embed(source, match):
    indent = match[1]
    body = "\n".join((indent + line).rstrip() for line in source)
    return f"{indent}// >>> arch_gate.js\n{body}\n{indent}// <<< arch_gate.js"


def main():
    source = (ROOT / "scripts/arch_gate.js").read_text(encoding="utf-8").splitlines()
    updates = {}
    for name in ("gate.yml", "merge-bot.yml"):
        path = WORKFLOWS / name
        original = path.read_text(encoding="utf-8")
        updated, count = BLOCK.subn(lambda match: embed(source, match), original)
        if count != 1:
            raise SystemExit(f"{name}: expected one arch_gate.js block, found {count}")
        if updated != original:
            updates[path] = updated
    for path, updated in updates.items():
        path.write_text(updated, encoding="utf-8")
        print(f"{path.name}: synchronized arch_gate.js")


if __name__ == "__main__":
    main()
