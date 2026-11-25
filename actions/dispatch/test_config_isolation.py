"""Keep credentialed publication independent of same-user configuration."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_dispatch import dispatch, git, REPOSITORY


class ConfigIsolationTests(unittest.TestCase):
    def test_git_run_ignores_user_fsmonitor_for_local_and_network_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            work = root / "work"
            git(root, "init", str(work))
            git(work, "config", "core.autocrlf", "false")
            (work / "tracked.txt").write_text("Tracked content\n", encoding="utf-8")
            git(work, "add", ".")
            git(work, "commit", "-m", "Initial content")
            marker = root / "fsmonitor-executed"
            script = root / "fsmonitor"
            script.write_text("#!/bin/sh\nprintf executed > " + shlex.quote(marker.as_posix()) + "\n", encoding="utf-8")
            script.chmod(0o755)
            git(root, "config", "--file", str(home / ".gitconfig"), "core.fsmonitor", script.as_posix())
            settings = dispatch.Settings(REPOSITORY, "test-token", work)
            runner = dispatch.Git(settings, root / "empty-hooks")
            with patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                                         "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}):
                for network in (False, True):
                    with self.subTest(network=network):
                        marker.unlink(missing_ok=True)
                        self.assertEqual(runner.run("status", "--porcelain", cwd=work, network=network), "")
                        self.assertFalse(marker.exists(), "Publication must ignore user fsmonitor commands")

    def test_token_gh_calls_use_fresh_empty_config_outside_home(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            config = home / ".config/gh"
            config.mkdir(parents=True)
            (config / "config.yml").write_text("http_unix_socket: " + (root / "untrusted.sock").as_posix() + "\n")
            settings = dispatch.Settings(REPOSITORY, "publication-token", root / "work",
                                         auth_secret_token="writeback-token")
            api = dispatch.GitHub(settings)
            auth = root / "auth.json"
            auth.write_text('{"refresh_token":"example-refresh"}')
            paths = []

            def inspect(command, **kwargs):
                env = kwargs["env"]
                self.assertIn("GH_CONFIG_DIR", env)
                path = Path(env["GH_CONFIG_DIR"])
                self.assertTrue(path.is_dir())
                self.assertEqual(list(path.iterdir()), [])
                self.assertFalse(path.is_relative_to(home))
                self.assertNotIn(path, paths, "Each call must create configuration after the previous caller exits")
                paths.append(path)
                (path / "config.yml").write_text("http_unix_socket: stale.sock\n")
                return subprocess.CompletedProcess(command, 0, "[]" if "--paginate" in command else "{}", "")

            with patch.dict(os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                                         "GH_CONFIG_DIR": str(config)}), patch.object(dispatch, "execute", side_effect=inspect):
                api.repo_info()
                api.list_comments(7)
                dispatch.write_auth_secret(settings, auth)
                api.comment(7, "Task finished after the adapter exited.")
            self.assertEqual(len(paths), 4)
            self.assertTrue(all(not path.exists() for path in paths))


if __name__ == "__main__":
    unittest.main()
