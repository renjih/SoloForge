"""Verify board publication ignores configuration planted by an adapter."""

import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from test_dispatch_hook import dispatch_hook, repository, git


class BoardConfigIsolationTests(unittest.TestCase):
    def test_post_publication_ignores_adapter_user_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            remote = root / "remote.git"
            git(root, "init", "--bare", "--initial-branch=main", str(remote))
            instruction = root / "instruction.md"
            instruction.write_text("Implement the task.\n")
            summary = root / "summary.md"
            summary.write_text("Implemented the task.\n")
            env = {"SF_REPOSITORY": "example-maintainer/example-game", "SF_OWNER": "example-maintainer",
                   "SF_WORKDIR": str(work), "SF_INSTRUCTION_FILE": str(instruction),
                   "SF_SUMMARY_FILE": str(summary), "SF_TASK_ID": "123", "SF_BRANCH": "task/example",
                   "SF_ISSUE": "7", "SF_ADAPTER": "echo", "GH_TOKEN": "test-token", "SF_RESULT": "done"}
            real_checkout = repository.checkout

            def local_checkout(repo, path, token, **kwargs):
                return real_checkout(repo, path, token, remote_url=str(remote), **kwargs)

            before = []
            after = []
            real_board = dispatch_hook.board

            def record(arguments, call_env):
                before.append(call_env.get("GH_CONFIG_DIR"))
                return real_board(arguments, call_env)

            with patch.dict(os.environ, env), patch.object(dispatch_hook, "checkout", side_effect=local_checkout):
                with patch.object(dispatch_hook, "board", side_effect=record):
                    dispatch_hook.run("pre")
                home = root / "home"
                config = home / ".config/gh"
                config.mkdir(parents=True)
                (config / "config.yml").write_text("http_unix_socket: " + (root / "untrusted.sock").as_posix() + "\n")
                marker = root / "user-fsmonitor-executed"
                script = root / "fsmonitor"
                script.write_text("#!/bin/sh\nprintf executed > " + shlex.quote(marker.as_posix()) + "\n", encoding="utf-8")
                script.chmod(0o755)
                git(root, "config", "--file", str(home / ".gitconfig"), "core.fsmonitor", script.as_posix())

                def inspect(arguments, call_env):
                    path = Path(call_env["GH_CONFIG_DIR"])
                    self.assertTrue(path.is_dir())
                    self.assertEqual(list(path.iterdir()), [])
                    self.assertFalse(path.is_relative_to(home))
                    self.assertNotIn(str(path), before, "Post configuration must be created after pre-task configuration")
                    after.append(call_env.copy())
                    return real_board(arguments, call_env)

                with patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                                             "GH_CONFIG_DIR": str(config)}):
                    with patch.object(dispatch_hook, "board", side_effect=inspect):
                        dispatch_hook.run("post")
            self.assertFalse(marker.exists(), "Post publication must ignore adapter-written fsmonitor configuration")
            self.assertTrue(after)
            for call_env in after:
                self.assertEqual(call_env["GIT_CONFIG_GLOBAL"], os.devnull)
                self.assertEqual(call_env["GIT_CONFIG_NOSYSTEM"], "1")
                self.assertNotIn(call_env["GH_CONFIG_DIR"], before)

    def test_board_token_calls_receive_empty_config_outside_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            config = home / ".config/gh"
            config.mkdir(parents=True)
            (config / "config.yml").write_text("http_unix_socket: untrusted.sock\n")
            real_run = repository.subprocess.run
            paths = []

            def inspect(command, **kwargs):
                env = kwargs["env"]
                self.assertEqual(env["GH_TOKEN"], "test-token")
                self.assertIn("GH_CONFIG_DIR", env)
                path = Path(env["GH_CONFIG_DIR"])
                self.assertTrue(path.is_dir())
                self.assertEqual(list(path.iterdir()), [])
                self.assertFalse(path.is_relative_to(home))
                paths.append(path)
                return real_run(command, **kwargs)

            with patch.dict(os.environ, {"HOME": str(home), "GH_CONFIG_DIR": str(config)}):
                with patch.object(repository.subprocess, "run", side_effect=inspect):
                    self.assertEqual(repository.run_git(["--version"], token="test-token").returncode, 0)
            self.assertTrue(all(not path.exists() for path in paths))


if __name__ == "__main__":
    unittest.main()
