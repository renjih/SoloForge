"""Board repository isolation and trusted dispatcher hook integration."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import repository
import dispatch_hook
import board as board_tools


def git(cwd, *args):
    result = subprocess.run(["git", "-c", "user.name=Example Maintainer", "-c",
                             "user.email=maintainer@example.invalid", *args], cwd=cwd,
                            capture_output=True, text=True, encoding="utf-8", env=repository.environment(""))
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class RepositoryTests(unittest.TestCase):
    def test_explicit_post_hooks_override_checkout_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            hooks = root / "new-empty-hooks"
            hooks.mkdir()
            git(root, "init", str(checkout))
            git(checkout, "config", "core.hooksPath", str(root / "untrusted-hooks"))
            result = repository.run_git(["config", "--get", "core.hooksPath"], cwd=checkout,
                                        token="test-token", hooks_path=hooks)
            self.assertEqual(Path(result.stdout.strip()), hooks)
            with patch.dict(os.environ, {"SOLOFORGE_GIT_HOOKS": str(hooks)}):
                result = board_tools.git("config", "--get", "core.hooksPath", cwd=checkout)
            self.assertEqual(Path(result.stdout.strip()), hooks)

    def test_missing_board_starts_without_application_history_and_reclones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote.git"
            git(root, "init", "--bare", "--initial-branch=main", str(remote))
            seed = root / "seed"
            git(root, "clone", str(remote), str(seed))
            (seed / "application.txt").write_text("application content")
            git(seed, "add", ".")
            git(seed, "commit", "-m", "Application history")
            git(seed, "push", "origin", "main")
            board = repository.checkout("example-maintainer/example-game", root / "board", "test-token", remote_url=str(remote))
            self.assertFalse((board / "application.txt").exists())
            (board / "cards").mkdir()
            (board / "cards/example.json").write_text("{}")
            git(board, "add", ".")
            git(board, "commit", "-m", "Initial board data")
            git(board, "push", "origin", "HEAD:board")
            self.assertEqual(git(board, "rev-list", "--count", "HEAD"), "1")
            clone = repository.checkout("example-maintainer/example-game", root / "copy", "test-token", remote_url=str(remote))
            self.assertTrue((clone / "cards/example.json").is_file())
            self.assertNotIn("test-token", (clone / ".git/config").read_text())
            with self.assertRaises(repository.RepositoryError):
                repository.checkout("example-maintainer/example-game", clone, "test-token", remote_url=str(remote))


class DispatchHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.instruction = self.root / "instruction.md"
        self.instruction.write_text("Implement the task.\n")
        self.summary = self.root / "summary.md"
        self.env = {"SF_REPOSITORY": "example-maintainer/example-game", "SF_OWNER": "example-maintainer",
                    "SF_WORKDIR": str(self.work), "SF_INSTRUCTION_FILE": str(self.instruction),
                    "SF_SUMMARY_FILE": str(self.summary), "SF_TASK_ID": "123", "SF_BRANCH": "task/example",
                    "SF_ISSUE": "7", "SF_ADAPTER": "echo", "GH_TOKEN": "test-token"}

    def test_pre_initializes_card_and_appends_context_outside_the_worktree(self):
        with patch.dict(os.environ, self.env), patch.object(dispatch_hook, "checkout") as clone:
            with patch.object(dispatch_hook, "board", side_effect=["example-card", "Board context", ""]) as call:
                dispatch_hook.run("pre")
        self.assertEqual(clone.call_args.args[0], self.env["SF_REPOSITORY"])
        self.assertEqual(call.call_args_list[0].args[0][0], "new")
        self.assertIn("in_progress", call.call_args_list[0].args[0])
        self.assertIn("Board context", self.instruction.read_text())
        self.assertFalse((self.work / "board-hook.json").exists())

    def test_post_pause_marks_blocked_and_attaches_the_pr(self):
        (self.root / "board-hook.json").write_text(json.dumps({"card_id": "example-card"}))
        self.summary.write_text("Work paused.")
        def inspect_post(arguments, env):
            directory = Path(env["BOARD_DIR"])
            hooks = Path(env["SOLOFORGE_GIT_HOOKS"])
            self.assertNotEqual(directory, self.root / "board")
            self.assertFalse(hooks.is_relative_to(self.root / "board"))
            self.assertTrue(hooks.is_dir())
            self.assertEqual(list(hooks.iterdir()), [])
            return ""
        with patch.dict(os.environ, {**self.env, "SF_RESULT": "paused", "SF_PR_URL": "https://github.com/example-maintainer/example-game/pull/8"}):
            with patch.object(dispatch_hook, "checkout") as clone, patch.object(dispatch_hook, "board", side_effect=inspect_post) as call:
                dispatch_hook.run("post")
        hooks = clone.call_args.kwargs["hooks_path"]
        self.assertEqual(str(hooks), call.call_args.args[1]["SOLOFORGE_GIT_HOOKS"])
        self.assertFalse(hooks.exists(), "The post-only hooks directory is cleaned up after publication")
        commands = [item.args[0] for item in call.call_args_list]
        self.assertIn("--result", commands[0])
        self.assertIn("paused", commands[0])
        self.assertEqual(commands[1][:2], ["set", "example-card"])
        self.assertNotIn("--status", commands[1], "Preserve the handoff state chosen by task-finish")
        self.assertIn("--pr", commands[1])

    def test_owner_mismatch_fails_before_checkout(self):
        with patch.dict(os.environ, {**self.env, "SF_OWNER": "another-maintainer"}), patch.object(dispatch_hook, "checkout") as clone:
            with self.assertRaises(repository.RepositoryError):
                dispatch_hook.run("pre")
        clone.assert_not_called()

    def test_real_hooks_publish_one_card_on_the_orphan_branch(self):
        remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "--initial-branch=main", str(remote))
        git(self.work, "init", "--initial-branch=main")
        (self.work / "README.md").write_text("# Example game\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "-m", "Application history")
        git(self.work, "remote", "add", "origin", str(remote))
        git(self.work, "push", "origin", "main")
        baseline = git(remote, "rev-parse", "main")
        real_checkout = repository.checkout
        def local_checkout(repo, directory, token, **kwargs):
            return real_checkout(repo, directory, token, remote_url=str(remote), **kwargs)
        with patch.dict(os.environ, self.env), patch.object(dispatch_hook, "checkout", side_effect=local_checkout):
            dispatch_hook.run("pre")
        (self.work / "change.txt").write_text("Implemented task\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "-m", "Task change")
        self.summary.write_text("Implemented task; project checks passed.\n")
        marker = self.root / "old-board-hook-executed"
        old_hooks = self.root / "board/.git/disabled-hooks"
        old_hooks.mkdir()
        injected = old_hooks / "pre-commit"
        injected.write_text("#!/usr/bin/env bash\nprintf executed > " + shlex.quote(marker.as_posix()) + "\n", encoding="utf-8")
        injected.chmod(0o755)
        concurrent = self.root / "concurrent-board"
        git(self.root, "clone", "--branch", "board", str(remote), str(concurrent))
        concurrent_path = next(concurrent.glob("cards/*/*.json"))
        concurrent_card = json.loads(concurrent_path.read_text())
        concurrent_card["outside_update"] = "Preserve changes made after the pre hook"
        concurrent_path.write_text(json.dumps(concurrent_card))
        git(concurrent, "add", ".")
        git(concurrent, "commit", "-m", "Concurrent board update")
        git(concurrent, "push", "origin", "board")
        with patch.dict(os.environ, {**self.env, "SF_RESULT": "done", "SF_PR_URL": "https://github.com/example-maintainer/example-game/pull/8"}):
            with patch.object(dispatch_hook, "checkout", side_effect=local_checkout) as clone:
                dispatch_hook.run("post")
        self.assertNotEqual(clone.call_args.args[1], self.root / "board")
        self.assertFalse(marker.exists(), "Post must never execute hooks planted in the adapter-visible board checkout")
        paths = [path for path in git(remote, "ls-tree", "-r", "--name-only", "board").splitlines() if path.startswith("cards/") and path.endswith(".json")]
        self.assertEqual(len(paths), 1)
        card = json.loads(git(remote, "show", "board:" + paths[0]))
        self.assertEqual(card["status"], "in_progress")
        self.assertEqual(card["pr"], 8)
        self.assertEqual(card["outside_update"], "Preserve changes made after the pre hook")
        for state in ("blocked", "needs_owner"):
            handoff = {"id": card["id"], "status": state, "today": "Implementation prepared.",
                       "state": "Waiting for the recorded next action.", "next": "Wait for the owner response.",
                       "blocker": "A dependency is unavailable.", "ruled_out": ["Automatic retry is disabled."],
                       "question": "Which follow-up should run?", "options": ["A: Resume", "B: Pause"],
                       "recommended": "B", "tier": "wait_for_owner"}
            self.summary.write_text("Task handoff.\n```board\n" + json.dumps(handoff) + "\n```\n")
            # An adapter must not redirect credentialed board publication.
            git(self.root / "board", "config", "remote.origin.pushurl", str(self.root / "untrusted.git"))
            with patch.dict(os.environ, {**self.env, "SF_RESULT": "done", "SF_PR_URL": "https://github.com/example-maintainer/example-game/pull/8"}):
                with patch.object(dispatch_hook, "checkout", side_effect=local_checkout):
                    dispatch_hook.run("post")
            card = json.loads(git(remote, "show", "board:" + paths[0]))
            self.assertEqual(card["status"], state, "The post hook must preserve a valid handoff state")
            self.assertEqual(card["next"], handoff["next"])
        self.assertEqual(git(remote, "rev-parse", "main"), baseline)


if __name__ == "__main__":
    unittest.main()
