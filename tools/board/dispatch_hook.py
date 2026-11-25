#!/usr/bin/env python3
"""Connect the dispatcher's trusted pre/post hooks to the caller's board branch."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from repository import RepositoryError, checkout, environment


HERE = Path(__file__).resolve().parent


def board(arguments, env):
    result = subprocess.run([sys.executable, str(HERE / "board.py"), *arguments],
                            cwd=env["SF_WORKDIR"], env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    if result.returncode:
        raise RepositoryError("The dispatcher board hook failed.")
    return result.stdout.strip()


def run(mode):
    with tempfile.TemporaryDirectory(prefix="soloforge-board-hook-gh-") as config:
        _run(mode, config)


def _run(mode, gh_config_dir):
    required = ("SF_REPOSITORY", "SF_OWNER", "SF_WORKDIR", "SF_INSTRUCTION_FILE", "SF_TASK_ID", "SF_BRANCH", "SF_ISSUE")
    if any(not os.environ.get(key) for key in required):
        raise RepositoryError("Dispatcher board variables are missing.")
    repository = os.environ["SF_REPOSITORY"]
    if repository.split("/")[0] != os.environ["SF_OWNER"]:
        raise RepositoryError("Board owner must match the caller repository.")
    temporary = Path(os.environ["SF_INSTRUCTION_FILE"]).resolve().parent
    directory = temporary / "board"
    state_path = temporary / "board-hook.json"
    agent = os.environ.get("SF_ADAPTER", "adapter")
    env = environment(os.environ.get("GH_TOKEN", ""), gh_config_dir)
    env.update({key: value for key, value in os.environ.items() if key.startswith("SF_")
                and key not in ("SF_GITHUB_TOKEN", "SF_AUTH_SECRET_TOKEN")})
    env.update(SOLOFORGE_OWNER=os.environ["SF_OWNER"], BOARD_DIR=str(directory),
               BOARD_REPO=repository.split("/")[1], BOARD_ACTOR=agent)
    if mode == "pre":
        checkout(repository, directory, env["GH_TOKEN"])
        candidates = [json.loads(path.read_text(encoding="utf-8"))
                      for path in (directory / "cards" / env["BOARD_REPO"]).glob("*.json")]
        candidates = [card for card in candidates if card.get("branch") == env["SF_BRANCH"] and card.get("status") != "done"]
        next_step = "Implement the authorized instruction and run project checks."
        pending_owner = False
        if candidates:
            card = max(candidates, key=lambda card: card.get("updated_at", ""))
            card_id = card["id"]
            pending_owner = card.get("status") == "needs_owner" and not (card.get("decision") or {}).get("answer")
            if pending_owner:
                board(["set", card_id, "--note", "Dispatch started while an owner decision is pending", "--by", agent], env)
            else:
                board(["set", card_id, "--status", "in_progress", "--next", next_step, "--by", agent], env)
        else:
            card_id = board(["new", "--repo", env["BOARD_REPO"], "--title", "Dispatch issue " + env["SF_ISSUE"],
                             "--status", "in_progress", "--assignee", agent, "--branch", env["SF_BRANCH"], "--by", agent,
                             "--next", next_step], env)
        state_path.write_text(json.dumps({"card_id": card_id}), encoding="utf-8")
        context = board(["task-start", "--session", env["SF_TASK_ID"], "--agent", agent], env)
        with Path(env["SF_INSTRUCTION_FILE"]).open("a", encoding="utf-8") as stream:
            stream.write("\n\n" + context + "\n\nUpdate dispatch card " + card_id + " in your handoff.\n")
            if pending_owner:
                stream.write("This card is waiting for an owner decision; this dispatch does not count as an answer.\n")
        board(["push", "-m", "board: dispatch started"], env)
    elif mode == "post":
        if not state_path.is_file():
            return
        # The adapter could alter every file in the pre-task board checkout.
        # Reclone data and create a separate empty hooks directory after it exits.
        with tempfile.TemporaryDirectory(prefix="soloforge-board-post-") as post_name:
            post_root = Path(post_name)
            hooks_path = post_root / "disabled-hooks"
            hooks_path.mkdir(mode=0o700)
            directory = post_root / "board"
            checkout(repository, directory, env["GH_TOKEN"], hooks_path=hooks_path)
            env.update(BOARD_DIR=str(directory), SOLOFORGE_GIT_HOOKS=str(hooks_path))
            card_id = json.loads(state_path.read_text(encoding="utf-8"))["card_id"]
            result = os.environ.get("SF_RESULT", "failed")
            summary = Path(env["SF_SUMMARY_FILE"])
            if not summary.exists():
                summary.write_text("Dispatcher result: " + result + "\n", encoding="utf-8")
            args = ["task-finish", "--summary", str(summary), "--branch", env["SF_BRANCH"], "--agent", agent, "--result", result]
            match = re.search(r"/pull/([1-9][0-9]*)$", os.environ.get("SF_PR_URL", ""))
            if match:
                args += ["--pr", match[1]]
            board(args, env)
            args = ["set", card_id, "--by", agent, "--note", "Dispatcher handoff recorded."]
            if match:
                args += ["--pr", match[1], "--url", os.environ["SF_PR_URL"]]
            board(args, env)
            board(["push", "-m", "board: dispatch handoff"], env)
    else:
        raise RepositoryError("Board hook mode must be pre or post.")


def main():
    try:
        if len(sys.argv) != 2:
            raise RepositoryError("Usage: dispatch_hook.py pre|post")
        run(sys.argv[1])
        return 0
    except (RepositoryError, OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("Dispatcher board integration failed; inspect the board before resuming.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
