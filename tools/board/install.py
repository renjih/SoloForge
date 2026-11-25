#!/usr/bin/env python3
"""Install board session hooks while preserving unrelated settings; do not commit."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil


HERE = Path(__file__).resolve().parent
BEGIN, END = "<!-- board:begin -->", "<!-- board:end -->"


def install(repo, owner, url):
    if not owner or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner):
        raise ValueError("Set SOLOFORGE_OWNER or pass --owner")
    if not url:
        raise ValueError("Pass --url for the repository containing the board tools and data")
    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise ValueError("The destination must be a Git checkout")
    settings_path = repo / ".claude/settings.json"
    current = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    hooks = current.setdefault("hooks", {})
    wanted = json.loads((HERE / "settings.json").read_text(encoding="utf-8"))["hooks"]
    for event, groups in wanted.items():
        existing = hooks.setdefault(event, [])
        existing[:] = [group for group in existing if "board-hook.sh" not in json.dumps(group)]
        existing.extend(groups)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    destination = repo / ".claude/hooks/board-hook.sh"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "board-hook.sh", destination)
    destination.chmod(0o755)
    configuration = repo / ".soloforge/board.json"
    configuration.parent.mkdir(parents=True, exist_ok=True)
    configuration.write_text(json.dumps({"owner": owner, "url": url}, indent=2) + "\n", encoding="utf-8")
    rules = (HERE.parents[1] / "rules/BOARD.md").read_text(encoding="utf-8")
    match = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), rules, re.S)
    if not match:
        raise ValueError("rules/BOARD.md must contain board rule block markers")
    agents = repo / "AGENTS.md"
    text = agents.read_text(encoding="utf-8") if agents.exists() else "# Implementer instructions\n"
    if BEGIN in text:
        text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: match[0], text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + match[0] + "\n"
    agents.write_text(text, encoding="utf-8")
    claude = repo / "CLAUDE.md"
    if claude.exists():
        text = claude.read_text(encoding="utf-8")
        if "Board handoff" not in text:
            claude.write_text(text.rstrip() + "\n\nBoard handoff: see the board section in AGENTS.md.\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--owner", default=os.environ.get("SOLOFORGE_OWNER"))
    parser.add_argument("--url", default=os.environ.get("SOLOFORGE_URL"))
    args = parser.parse_args()
    try:
        install(args.repo, args.owner, args.url)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    print("Installed board hooks and implementer instructions")


if __name__ == "__main__":
    main()
