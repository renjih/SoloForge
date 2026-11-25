"""Prepare an isolated board checkout without persisting repository credentials."""

import os
import json
from pathlib import Path
import re
import subprocess


class RepositoryError(Exception):
    pass


def environment(token):
    excluded = {"CODEX_AUTH_JSON", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN",
                "SF_GITHUB_TOKEN", "SF_AUTH_SECRET_TOKEN"}
    env = {key: value for key, value in os.environ.items()
           if key not in excluded and not key.startswith(("GIT_CONFIG_", "INPUT_", "ACTIONS_"))}
    env.update(GH_TOKEN=token, GH_HOST="github.com", GIT_TERMINAL_PROMPT="0")
    return env


def run_git(args, *, cwd=None, token="", check=True, hooks_path=None):
    command = ["git"]
    if hooks_path is not None:
        command += ["-c", "core.hooksPath=" + str(hooks_path)]
    response = subprocess.run([*command, "-c", "credential.helper=", "-c",
                               "credential.helper=!gh auth git-credential", *args], cwd=cwd,
                              env=environment(token), capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
    if check and response.returncode:
        raise RepositoryError("Board Git operation failed.")
    return response


def checkout(repository, directory, token, *, remote_url=None, hooks_path=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RepositoryError("An explicit owner/repository is required.")
    directory = Path(directory).resolve()
    if directory.exists():
        raise RepositoryError("The board checkout requires a fresh directory.")
    url = remote_url or f"https://github.com/{repository}.git"
    remote = run_git(["ls-remote", "--exit-code", "--heads", url, "refs/heads/board"],
                     cwd=directory.parent, token=token, check=False, hooks_path=hooks_path)
    if remote.returncode not in (0, 2):
        raise RepositoryError("Could not inspect the remote board branch.")
    if remote.returncode == 0:
        run_git(["clone", "--single-branch", "--branch", "board", "--", url, str(directory)],
                cwd=directory.parent, token=token, hooks_path=hooks_path)
    else:
        run_git(["init", "--initial-branch=board", str(directory)], cwd=directory.parent,
                token=token, hooks_path=hooks_path)
        run_git(["remote", "add", "origin", url], cwd=directory, token=token, hooks_path=hooks_path)
    restore_config(repository, directory, remote_url=remote_url, hooks_path=hooks_path)
    return directory


def restore_config(repository, directory, *, remote_url=None, hooks_path=None):
    directory = Path(directory)
    metadata = directory / ".git"
    config = metadata / "config"
    if metadata.is_symlink() or not metadata.is_dir() or config.is_symlink():
        raise RepositoryError("Board metadata must be a regular isolated Git directory.")
    url = remote_url or f"https://github.com/{repository}.git"
    config.write_text(
        '[core]\n repositoryformatversion = 0\n bare = false\n logallrefupdates = true\n'
        + ' hooksPath = ' + json.dumps(str(hooks_path or metadata / "disabled-hooks"), ensure_ascii=False) + '\n'
        + '[remote "origin"]\n url = ' + json.dumps(url) + '\n fetch = +refs/heads/board:refs/remotes/origin/board\n'
        + '[branch "board"]\n remote = origin\n merge = refs/heads/board\n'
        + '[user]\n name = SoloForge Automation\n email = noreply@example.invalid\n'
        + '[credential]\n helper =\n helper = !gh auth git-credential\n', encoding="utf-8")
