#!/usr/bin/env python3
"""Maintain board data using trusted tools and an ephemeral orphan checkout."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import board
import repository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default=os.environ.get("SOLOFORGE_OWNER"))
    parser.add_argument("--repository", default=os.environ.get("SF_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--projects", type=Path)
    args = parser.parse_args()
    if not args.owner or not args.repository or args.repository.split("/", 1)[0] != args.owner:
        parser.exit(2, "Provide a repository and its owner through arguments or environment variables.\n")
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        parser.exit(2, "GH_TOKEN is required.\n")
    with tempfile.TemporaryDirectory(prefix="soloforge-board-maintenance-") as temporary:
        directory = Path(temporary) / "board"
        config = Path(temporary) / "gh-config"
        config.mkdir(mode=0o700)
        repository.checkout(args.repository, directory, token)
        env = {**repository.environment(token, config), "SOLOFORGE_OWNER": args.owner, "BOARD_DIR": str(directory),
               "BOARD_REPO": args.repository.split("/", 1)[1], "BOARD_ACTOR": "bot"}
        executable = [sys.executable, str(Path(board.__file__))]
        health = ["health", "--by", "workflow"]
        if args.projects:
            health += ["--projects", str(args.projects.resolve())]
        for command in (["expire"], ["sync-merged"], ["archive"], health, ["validate"], ["push", "-m", "board: scheduled maintenance"]):
            result = subprocess.run(executable + command, env=env, timeout=300)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
