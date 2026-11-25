#!/usr/bin/env python3
"""Dispatch authorized comments without evaluating event data as shell code."""

import base64
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
PROVIDER_SECRETS = ("CODEX_AUTH_JSON", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY",
                    "CODEX_ACCESS_TOKEN", "CODEX_HOME", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
PUBLICATION_SECRETS = ("GH_TOKEN", "GITHUB_TOKEN", "SF_GITHUB_TOKEN", "SF_AUTH_SECRET_TOKEN",
                      "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
TIERS = ("mechanical", "normal", "heavy", "ultra", "rework")
DEADLINE = ContextVar("dispatch_deadline", default=None)
ATTEMPT_MARKER = "<!-- soloforge:attempt:v1 -->"


class DispatchError(Exception):
    """Messages must be fixed descriptions, never subprocess output or credentials."""


class DispatchTimeout(DispatchError):
    """The total wall-time allowance has expired."""


def clean_env():
    return {key: value for key, value in os.environ.items()
            if key not in PROVIDER_SECRETS + PUBLICATION_SECRETS
            and not key.startswith(("GIT_CONFIG_", "GIT_ASKPASS", "SSH_ASKPASS", "ACTIONS_", "INPUT_", "SF_"))}


def execute_gh(command, token, **kwargs):
    with tempfile.TemporaryDirectory(prefix="soloforge-gh-") as config:
        return execute(command, env={**clean_env(), "GH_TOKEN": token, "GH_HOST": "github.com",
                                     "GH_CONFIG_DIR": config}, **kwargs)


def execute(command, *, cwd=None, env=None, input=None, timeout=120):
    if os.name == "nt" and command[0] == "bash":
        shell = shutil.which("bash")
        direct_shell = Path(shell).parent.parent / "usr/bin/bash.exe" if shell else None
        if direct_shell and direct_shell.is_file():
            command = [str(direct_shell), *command[1:]]
    deadline = DEADLINE.get()
    remaining = deadline - time.monotonic() if deadline is not None else None
    if remaining is not None:
        if remaining <= 0:
            raise DispatchTimeout("Dispatch stopped at its configured wall-time limit.")
        timeout = min(timeout, remaining)
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, text=True, encoding="utf-8",
                                   errors="replace", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=os.name == "posix")
    except OSError:
        if deadline is not None and time.monotonic() >= deadline:
            raise DispatchTimeout("Dispatch stopped at its configured wall-time limit.") from None
        raise DispatchError("A required command could not complete.") from None
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            # Allow supervised adapters to stop their independent provider group first.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            if process.poll() is None:
                process.kill()
        process.communicate()
        if deadline is not None and time.monotonic() >= deadline:
            raise DispatchTimeout("Dispatch stopped at its configured wall-time limit.") from None
        raise DispatchError("A required command could not complete.") from None


def executable(path):
    # Git Bash supports the bundled shell adapters in local Windows checks.
    if os.name == "nt" and path.read_bytes().startswith(b"#!/usr/bin/env bash"):
        return ["bash", str(path)]
    return [str(path)]


@dataclass
class Settings:
    repository: str
    token: str
    workdir: Path
    triggers: dict = field(default_factory=lambda: {"/codex": "codex", "/claude": "claude"})
    associations: list = field(default_factory=lambda: ["OWNER"])
    pre_task: str = ""
    post_task: str = ""
    auth_secret_token: str = ""
    action_root: Path = ROOT
    timeout_minutes: float = 30
    max_turns: int = 21
    max_tokens: int = 244000
    metrics_file: Path | None = None
    pause_reason: str = ""
    publication_started: bool = False
    publication_branch: str = ""
    publication_url: str = ""
    ledger_issue: int | None = None
    ledger_record: dict | None = None

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            raise DispatchError("A repository in owner/repository format is required.")
        if not self.token:
            raise DispatchError("A GitHub token is required.")
        if type(self.timeout_minutes) not in (int, float) or not math.isfinite(self.timeout_minutes) or not 0 < self.timeout_minutes <= 90:
            raise DispatchError("Timeout must be positive and at most 90 minutes.")
        if any(type(value) is not int or value < 1 for value in (self.max_turns, self.max_tokens)):
            raise DispatchError("Adapter turn and token budgets must be positive integers.")
        if not isinstance(self.associations, list) or not self.associations or any(
                not isinstance(item, str) or not item for item in self.associations):
            raise DispatchError("Allowed associations must be a nonempty JSON string list.")
        if not isinstance(self.triggers, dict) or not self.triggers:
            raise DispatchError("Triggers must be a nonempty JSON object.")
        for trigger, adapter in self.triggers.items():
            if not isinstance(trigger, str) or not re.fullmatch(r"/[^\s]+", trigger):
                raise DispatchError("Each trigger must be a slash command without spaces.")
            if not isinstance(adapter, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", adapter):
                raise DispatchError("Each adapter must be a simple directory name.")


class GitHub:
    def __init__(self, settings):
        self.repository = settings.repository
        self.token = settings.token

    def api(self, suffix="", body=None, missing=False, method=None):
        command = ["gh", "api", "--hostname", "github.com", "repos/" + self.repository + suffix]
        if body is not None:
            command += ["--method", method or "POST", "--input", "-"]
        response = execute_gh(command, self.token, input=json.dumps(body) if body is not None else None)
        if response.returncode:
            if missing and re.search(r"HTTP 404\b", response.stderr):
                return None
            raise DispatchError("GitHub API request failed.")
        try:
            return json.loads(response.stdout)
        except ValueError:
            raise DispatchError("GitHub API returned invalid JSON.") from None

    def repo_info(self):
        return self.api()

    def pull_request(self, number):
        return self.api(f"/pulls/{number}")

    def branch_exists(self, branch):
        return self.api("/git/ref/heads/" + quote(branch, safe=""), missing=True) is not None

    def comment(self, number, body):
        return self.api(f"/issues/{number}/comments", {"body": body})

    def list_comments(self, number):
        response = execute_gh(["gh", "api", "--hostname", "github.com", "--paginate", "--slurp",
                            f"repos/{self.repository}/issues/{number}/comments?per_page=100"],
                             self.token)
        if response.returncode:
            raise DispatchError("Could not read the dispatch rate-limit ledger.")
        try:
            pages = json.loads(response.stdout)
            if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
                raise ValueError
            return [comment for page in pages for comment in page]
        except (ValueError, TypeError):
            raise DispatchError("The dispatch rate-limit ledger is invalid.") from None

    def has_write_permission(self, login):
        result = self.api("/collaborators/" + quote(login, safe="") + "/permission", missing=True)
        return isinstance(result, dict) and result.get("permission") in ("admin", "maintain", "write")

    def retire_reservation(self, identifier):
        self.api(f"/issues/comments/{identifier}", {"body": "<!-- soloforge:refused:v1 -->\nDispatch start refused by the hourly limit."}, method="PATCH")

    def open_pr(self, branch, base, number, summary):
        return self.api("/pulls", {"head": branch, "base": base,
                        "title": f"SoloForge: issue {number}",
                        "body": f"Closes #{number}\n\n{summary}"})["html_url"]


class Git:
    def __init__(self, settings, hooks):
        self.settings = settings
        self.hooks = hooks

    def run(self, *args, cwd=None, network=False):
        env = {**clean_env(), "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1"}
        if network:
            basic = base64.b64encode(("x-access-token:" + self.settings.token).encode()).decode()
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=f"http.https://github.com/{self.settings.repository}.git.extraheader",
                       GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + basic)
        response = execute(["git", "-c", "core.hooksPath=" + str(self.hooks), *args],
                           cwd=cwd, env=env)
        if response.returncode:
            raise DispatchError("Git operation failed; inspect the checkout locally.")
        return response.stdout.strip()


def authorized_task(event, settings):
    if event.get("action") != "created" or "issue" not in event:
        return None
    if event.get("repository", {}).get("full_name") != settings.repository:
        raise DispatchError("Event repository does not match the action repository.")
    comment = event.get("comment") or {}
    if comment.get("author_association") not in settings.associations:
        return None
    body = comment.get("body") or ""
    for trigger in sorted(settings.triggers, key=len, reverse=True):
        if body.startswith(trigger) and len(body) > len(trigger) and body[len(trigger)].isspace():
            instruction = body[len(trigger):].strip()
            if not instruction:
                return None
            number, comment_id = event["issue"].get("number"), comment.get("id")
            if type(number) is not int or number < 1 or type(comment_id) is not int or comment_id < 1:
                raise DispatchError("Event issue and comment identifiers must be positive integers.")
            return {"adapter": settings.triggers[trigger], "instruction": instruction,
                    "number": number, "comment_id": comment_id,
                    "mode": "pr" if "pull_request" in event["issue"] else "issue"}
    return None


def merge_config(base, override):
    if not isinstance(override, dict):
        raise DispatchError("Model configuration must be a JSON object.")
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            merge_config(base[key], value)
        else:
            base[key] = value
    return base


def load_config(workdir, root, git, ref):
    try:
        config = json.loads((root / "actions/dispatch/defaults/models.json").read_text(encoding="utf-8"))
        path = ".soloforge/models.json"
        if git.run("ls-tree", "--name-only", ref, "--", path, cwd=workdir):
            merge_config(config, json.loads(git.run("show", ref + ":" + path, cwd=workdir)))
        return config
    except (OSError, ValueError, TypeError):
        raise DispatchError("Model configuration could not be loaded.") from None


def select_route(config, adapter, instruction, mode):
    match = re.search(r"^Tier:[ \t]*(mechanical|normal|heavy|ultra|rework)[ \t]*\r?$", instruction, re.M)
    tier = match[1] if match else ("normal" if mode == "issue" else "rework")
    try:
        selected = config["adapters"][adapter]["tiers"][tier]
        if any(not isinstance(selected.get(key), str) for key in ("model", "effort")):
            raise KeyError
        return {"tier": tier, "model": selected["model"], "effort": selected["effort"]}
    except (KeyError, TypeError):
        raise DispatchError("The adapter needs model and effort settings for the selected tier.") from None


def quota_decision(config, snapshot, mode, tier):
    try:
        limits = config["quota"]
        keys = ("five_hour_pause_remaining", "weekly_stop_remaining",
                "weekly_rework_only_remaining", "weekly_downgrade_remaining")
        values = [limits[key] for key in keys]
        if any(type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 100
               for value in values) or not values[1] <= values[2] <= values[3]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DispatchError("Quota thresholds must be ordered percentages from zero to 100.") from None

    def remaining(name):
        window = snapshot.get(name) if isinstance(snapshot, dict) else None
        used = window.get("used_percent") if isinstance(window, dict) else None
        return 100 - used if type(used) in (float, int) and math.isfinite(used) and 0 <= used <= 100 else None

    short, weekly = remaining("five_hour"), remaining("weekly")
    if short is not None and short <= values[0]:
        return "pause"
    if weekly is not None:
        if weekly <= values[1] or (weekly <= values[2] and mode == "issue"):
            return "pause"
        if weekly <= values[3] and tier in ("heavy", "ultra"):
            return "downgrade"
    return "run"


def public_quota(snapshot):
    result = {"read_at": datetime.now(timezone.utc).isoformat(), "five_hour": None, "weekly": None}
    for name in ("five_hour", "weekly"):
        window = snapshot.get(name) if isinstance(snapshot, dict) else None
        used = window.get("used_percent") if isinstance(window, dict) else None
        reset = window.get("resets_at") if isinstance(window, dict) else None
        if type(used) in (int, float) and math.isfinite(used) and 0 <= used <= 100:
            if type(reset) not in (int, float) or not math.isfinite(reset) or not 0 <= reset <= 253402300799:
                reset = None
            result[name] = {"used_percent": used, "resets_at": reset}
    return result


def quota_level(config, snapshot):
    limits = config["quota"]
    short, weekly = snapshot["five_hour"], snapshot["weekly"]
    if short and 100 - short["used_percent"] <= limits["five_hour_pause_remaining"]:
        return "5h_wait"
    if not weekly:
        return "unknown"
    remaining = 100 - weekly["used_percent"]
    for key, level in (("weekly_stop_remaining", "stopped"), ("weekly_rework_only_remaining", "rework_only"),
                       ("weekly_downgrade_remaining", "low")):
        if remaining <= limits[key]:
            return level
    return "normal"


def safe_summary(path, secrets):
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "Task completed."
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text[:4000].strip() or "Task completed."


def secret_values(value):
    if isinstance(value, dict):
        return [item for child in value.values() for item in secret_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in secret_values(child)]
    return [value] if isinstance(value, str) and len(value) >= 8 else []


def write_auth_secret(settings, auth_path):
    response = execute_gh(["gh", "secret", "set", "CODEX_AUTH_JSON", "--repo", settings.repository,
                        "--app", "actions"], input=auth_path.read_text(encoding="utf-8"),
                         token=settings.auth_secret_token)
    if response.returncode:
        raise DispatchError("Refreshed Codex login could not be saved to the caller repository Secret.")


def reconcile_auth(settings, auth_path, original, secrets):
    if original is None:
        return
    if not auth_path.is_file():
        raise DispatchError("The Codex adapter removed its login file.")
    refreshed = auth_path.read_text(encoding="utf-8")
    if refreshed != original:
        try:
            data = json.loads(refreshed)
            if not isinstance(data, dict):
                raise ValueError
            secrets += secret_values(data)
        except ValueError:
            raise DispatchError("Refreshed Codex login is invalid JSON.") from None
        budget = DEADLINE.set(None)
        try:
            write_auth_secret(settings, auth_path)
        finally:
            DEADLINE.reset(budget)


def adapter_environment(task, settings, temporary, route):
    env = clean_env()
    env.update(SF_WORKDIR=str(settings.workdir.resolve()), SF_REPOSITORY=settings.repository,
               SF_OWNER=settings.repository.split("/")[0], SF_ISSUE=str(task["number"]),
               SF_TASK_ID=str(task["comment_id"]), SF_MODE=task["mode"], SF_BRANCH=task["branch"],
               SF_ACTION_ROOT=str(settings.action_root), SF_MODEL=route["model"], SF_EFFORT=route["effort"],
               SF_TIER=route["tier"], SF_INSTRUCTION_FILE=str(temporary / "instruction.md"),
               SF_SUMMARY_FILE=str(temporary / "summary.md"), SF_LOG_FILE=str(temporary / "adapter.log"))
    env["SF_PAUSE_REASON_FILE"] = str(temporary / "pause-reason.txt")
    env["SF_ADAPTER"] = task["adapter"]
    env.update(SF_MAX_TURNS=str(settings.max_turns), SF_MAX_TOKENS=str(settings.max_tokens),
               SF_METRICS_FILE=str(settings.metrics_file or temporary / "metrics.json"))
    if task["adapter"] == "codex":
        env["CODEX_HOME"] = str(temporary / "codex-home")
        if os.environ.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    elif task["adapter"] == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
    elif task["adapter"] == "echo" and os.environ.get("SF_ECHO_EXIT"):
        env["SF_ECHO_EXIT"] = os.environ["SF_ECHO_EXIT"]
        if os.environ.get("SF_ECHO_PAUSE_REASON"):
            env["SF_ECHO_PAUSE_REASON"] = os.environ["SF_ECHO_PAUSE_REASON"]
    return env


def run_hook(command, env, settings, result, pr_url):
    if not command:
        return
    if not isinstance(command, str):
        raise DispatchError("Task hooks must be trusted shell command strings.")
    hook_env = {**clean_env(), **{key: value for key, value in env.items() if key.startswith("SF_")},
                "GH_TOKEN": settings.token, "SF_RESULT": result, "SF_PR_URL": pr_url}
    with tempfile.TemporaryDirectory(prefix="soloforge-hook-gh-") as config:
        hook_env["GH_CONFIG_DIR"] = config
        response = execute(["bash", "-e", "-o", "pipefail", "-c", command], cwd=settings.workdir,
                           env=hook_env, timeout=1200)
    if response.returncode:
        raise DispatchError("A task hook failed.")


def _dispatch(event, settings, github=None, clone_url=None):
    settings.validate()
    settings.workdir = settings.workdir.resolve()
    settings.action_root = settings.action_root.resolve()
    task = authorized_task(event, settings)
    if task is None:
        return {"result": "ignored", "branch": "", "pr_url": ""}
    github = github or GitHub(settings)
    repo = github.repo_info()
    if task["mode"] == "pr":
        pr = github.pull_request(task["number"])
        head, base = pr.get("head") or {}, pr.get("base") or {}
        if (head.get("repo") or {}).get("full_name") != (base.get("repo") or {}).get("full_name") or (
                base.get("repo") or {}).get("full_name") != settings.repository:
            github.comment(task["number"], "SoloForge refused this fork pull request. A repository owner must handle it.")
            return {"result": "refused", "branch": "", "pr_url": ""}
        if pr.get("state") != "open":
            raise DispatchError("Only open pull requests can receive dispatched work.")
        task["branch"] = head.get("ref", "")
        if task["branch"] == repo["default_branch"]:
            raise DispatchError("The dispatcher does not write to the default branch.")
        task["head_sha"] = head["sha"]
        pr_url = pr["html_url"]
    else:
        task["branch"] = f"soloforge/issue-{task['number']}-comment-{task['comment_id']}"
        pr_url = ""
        if github.branch_exists(task["branch"]):
            github.comment(task["number"], "This comment already has a task branch. Continue on its pull request.")
            return {"result": "duplicate", "branch": task["branch"], "pr_url": ""}

    adapter = settings.action_root / "adapters" / task["adapter"] / "run"
    if not adapter.is_file():
        raise DispatchError("The requested adapter is not installed in this Action.")
    if settings.workdir.exists():
        raise DispatchError("Work directory already exists; provide a fresh directory.")
    result = "failed"
    with tempfile.TemporaryDirectory(prefix="soloforge-") as temp_name:
        temporary = Path(temp_name)
        hooks = temporary / "git-hooks"
        hooks.mkdir()
        git = Git(settings, hooks)
        git.run("check-ref-format", "--branch", task["branch"])
        git.run("clone", "--no-tags", "--", clone_url or f"https://github.com/{settings.repository}.git",
                str(settings.workdir), network=True)
        trusted_config_ref = git.run("rev-parse", "--verify", "refs/remotes/origin/" + repo["default_branch"] + "^{commit}", cwd=settings.workdir)
        if task["mode"] == "pr":
            git.run("checkout", "-B", task["branch"], "origin/" + task["branch"], cwd=settings.workdir)
            if git.run("rev-parse", "HEAD", cwd=settings.workdir) != task["head_sha"]:
                raise DispatchError("Pull request changed before checkout; dispatch again.")
        else:
            git.run("checkout", "-b", task["branch"], "origin/" + repo["default_branch"], cwd=settings.workdir)
        git_config = settings.workdir / ".git/config"
        trusted_git_config = git_config.read_bytes()
        config = load_config(settings.workdir, settings.action_root, git, trusted_config_ref)
        route = select_route(config, task["adapter"], task["instruction"], task["mode"])
        quota_decision(config, {}, task["mode"], route["tier"])
        settings.ledger_issue = config.get("quota", {}).get("ledger_issue")
        if settings.ledger_issue is not None and (type(settings.ledger_issue) is not int or settings.ledger_issue < 1):
            raise DispatchError("quota.ledger_issue must be a positive issue number in the caller repository.")
        if settings.ledger_issue is not None:
            settings.ledger_record = {"schema": 1, "repo": settings.repository, "number": task["number"],
                                      "branch": task["branch"], "mode": task["mode"], "adapter": task["adapter"],
                                      "tier": route["tier"], "model": route["model"] or task["adapter"] + ":default",
                                      "effort": route["effort"], "level": "unknown", "quota_before": None, "quota_after": None}
        env = adapter_environment(task, settings, temporary, route)
        prompt = ("Read AGENTS.md when present. Make changes and run the repository checks. "
                  "Do not commit, push, merge, or open pull requests; the dispatcher publishes your changes.\n\n"
                  + task["instruction"] + "\n")
        Path(env["SF_INSTRUCTION_FILE"]).write_text(prompt, encoding="utf-8")
        secrets = [settings.token, settings.auth_secret_token] + [os.environ.get(key, "") for key in PROVIDER_SECRETS]
        auth_path = Path(env.get("CODEX_HOME", str(temporary))) / "auth.json"
        original_auth = None
        if task["adapter"] == "codex" and not env.get("OPENAI_API_KEY") and os.environ.get("CODEX_AUTH_JSON"):
            if not settings.auth_secret_token:
                raise DispatchError("Codex login requires auth-secret-token to save refreshed credentials.")
            try:
                auth = json.loads(os.environ["CODEX_AUTH_JSON"])
                if not isinstance(auth, dict):
                    raise ValueError
            except ValueError:
                raise DispatchError("CODEX_AUTH_JSON must contain a JSON object.") from None
            auth_path.parent.mkdir(mode=0o700)
            original_auth = json.dumps(auth)
            with auth_path.open("w", encoding="utf-8") as stream:
                stream.write(original_auth)
            auth_path.chmod(0o600)
            secrets += secret_values(auth)
        pre = settings.pre_task or config.get("hooks", {}).get("pre_task", "")
        post = settings.post_task or config.get("hooks", {}).get("post_task", "")
        try:
            run_hook(pre, env, settings, "running", pr_url)
            try:
                quota_command = adapter.with_name("quota")
                decision = "run"
                if quota_command.is_file():
                    snapshot = execute(executable(quota_command), cwd=settings.workdir, env=env, timeout=60)
                    try:
                        data = json.loads(snapshot.stdout) if snapshot.returncode == 0 else {}
                    except ValueError:
                        data = {}
                    decision = quota_decision(config, data, task["mode"], route["tier"])
                    if settings.ledger_record is not None:
                        settings.ledger_record["quota_before"] = public_quota(data)
                        settings.ledger_record["level"] = quota_level(config, settings.ledger_record["quota_before"])
                if decision == "downgrade":
                    lower = config["adapters"][task["adapter"]]["downgrade"]
                    if any(not isinstance(lower.get(key), str) for key in ("model", "effort")):
                        raise DispatchError("Downgrade settings need model and effort strings.")
                    env.update(SF_MODEL=lower["model"], SF_EFFORT=lower["effort"])
                    if settings.ledger_record is not None:
                        settings.ledger_record.update(model=lower["model"] or task["adapter"] + ":default", effort=lower["effort"])
                remaining = DEADLINE.get() - time.monotonic() if DEADLINE.get() else settings.timeout_minutes * 60
                env["SF_TIMEOUT_MINUTES"] = str(max(.001, remaining - 1) / 60)
                code = 75 if decision == "pause" else execute(executable(adapter), cwd=settings.workdir,
                                                              env=env, timeout=3600).returncode
                if settings.ledger_record is not None and quota_command.is_file() and DEADLINE.get() - time.monotonic() > 35:
                    try:
                        after = execute(executable(quota_command), cwd=settings.workdir, env=env, timeout=35)
                        data = json.loads(after.stdout) if after.returncode == 0 else {}
                        normalized = public_quota(data)
                        if normalized["weekly"] or normalized["five_hour"]:
                            settings.ledger_record["quota_after"] = normalized
                            settings.ledger_record["level"] = quota_level(config, normalized)
                    except (ValueError, DispatchError):
                        print("::warning::Post-task quota telemetry unavailable.", file=sys.stderr)
            finally:
                reconcile_auth(settings, auth_path, original_auth, secrets)
            if code == 75:
                result = "paused"
                reason = Path(env["SF_PAUSE_REASON_FILE"])
                supplied = reason.read_text(encoding="utf-8").strip() if reason.is_file() else "adapter"
                settings.pause_reason = "quota" if decision == "pause" else supplied if supplied in ("quota", "budget") else "adapter"
                github.comment(task["number"], "SoloForge paused: " + pause_description(settings.pause_reason)
                               + " No changes were committed or pushed.")
            elif code:
                raise DispatchError("The adapter failed. No changes were published.")
            else:
                if git.run("rev-parse", "--abbrev-ref", "HEAD", cwd=settings.workdir) != task["branch"]:
                    raise DispatchError("The adapter changed branches; publication was refused.")
                # Restore config, including push URLs and URL rewrites, before publication.
                git_config.write_bytes(trusted_git_config)
                git.run("remote", "set-url", "origin", clone_url or f"https://github.com/{settings.repository}.git", cwd=settings.workdir)
                git.run("add", "--all", cwd=settings.workdir)
                changes = git.run("diff", "--cached", "--name-only", cwd=settings.workdir)
                if not changes:
                    result = "no_change"
                    github.comment(task["number"], "SoloForge completed the task without file changes.")
                else:
                    git.run("-c", "user.name=SoloForge Automation", "-c", "user.email=noreply@example.invalid",
                            "commit", "-m", f"SoloForge: issue {task['number']}", cwd=settings.workdir)
                    settings.publication_started = True
                    settings.publication_branch = task["branch"]
                    settings.publication_url = pr_url
                    git.run("push", "origin", "HEAD:refs/heads/" + task["branch"], cwd=settings.workdir, network=True)
                    summary = safe_summary(Path(env["SF_SUMMARY_FILE"]), secrets)
                    if task["mode"] == "issue":
                        pr_url = github.open_pr(task["branch"], repo["default_branch"], task["number"], summary)
                        settings.publication_url = pr_url
                    github.comment(task["number"], "SoloForge completed the task.\n\n" + summary + "\n\n" + pr_url)
                    result = "done"
        finally:
            if DEADLINE.get() is None or time.monotonic() < DEADLINE.get():
                run_hook(post, env, settings, result, pr_url)
    return {"result": result, "branch": task["branch"], "pr_url": pr_url}


def attempt_rows(comments, now, accepts):
    rows = []
    for comment in comments:
        if not (comment.get("body") or "").startswith(ATTEMPT_MARKER + "\n"):
            continue
        if not accepts(comment):
            continue
        try:
            stamp = datetime.fromisoformat(comment["created_at"].replace("Z", "+00:00"))
            identifier = comment["id"]
            if stamp.tzinfo is None or type(identifier) is not int:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise DispatchError("A dispatch reservation has invalid server metadata.") from None
        if now - timedelta(hours=1) < stamp <= now:
            rows.append((stamp, identifier))
    return sorted(rows)


def reserve_attempt(github, number):
    reservation = github.comment(number, ATTEMPT_MARKER + "\nSoloForge task start reserved.")
    actor = reservation.get("user") or {}
    bot_id = actor.get("id") if actor.get("type") == "Bot" else None
    permissions = {}

    def accepts(comment):
        user = comment.get("user") or {}
        if type(bot_id) is int and user.get("type") == "Bot" and user.get("id") == bot_id:
            return True
        login = user.get("login")
        if not isinstance(login, str) or not login:
            return False
        if login not in permissions:
            permissions[login] = github.has_write_permission(login)
        return permissions[login]
    try:
        stamp = datetime.fromisoformat(reservation["created_at"].replace("Z", "+00:00"))
        key = (stamp, reservation["id"])
    except (KeyError, ValueError, TypeError):
        raise DispatchError("GitHub did not confirm a dispatch reservation.") from None
    rows = attempt_rows(github.list_comments(number), stamp, accepts)
    # Refetch after reserving so concurrent callers agree on the first three starts.
    if key not in rows:
        raise DispatchError("The dispatch reservation was not found after creation.")
    if rows.index(key) >= 3:
        # Do not extend the cooldown with rejected starts.
        github.retire_reservation(reservation["id"])
        return False
    return True


def pause_description(reason):
    return {"quota": "Provider quota is insufficient.",
            "budget": "The execution budget limit was reached (time, items, or tokens)."}.get(
                reason, "The adapter requested a pause without a classified reason.")


def dispatch(event, settings, github=None, clone_url=None):
    settings.validate()
    task = authorized_task(event, settings)
    if task is None:
        return {"result": "ignored", "branch": "", "pr_url": ""}
    github = github or GitHub(settings)
    start = time.monotonic()
    settings.publication_started = False
    settings.publication_branch = settings.publication_url = ""
    settings.pause_reason = ""
    settings.ledger_issue = settings.ledger_record = None
    outcome = {"result": "failed", "branch": "", "pr_url": ""}
    with tempfile.TemporaryDirectory(prefix="soloforge-metrics-") as directory:
        settings.metrics_file = Path(directory) / "metrics.json"
        settings.metrics_file.write_text('{"turns":0,"tokens":null}', encoding="utf-8")
        budget = DEADLINE.set(start + settings.timeout_minutes * 60)
        try:
            if not reserve_attempt(github, task["number"]):
                outcome["result"] = "rate_limited"
            else:
                outcome = _dispatch(event, settings, github, clone_url)
            return outcome
        except DispatchTimeout:
            settings.pause_reason = "budget"
            outcome.update(result="publication_unknown" if settings.publication_started else "paused",
                           branch=settings.publication_branch, pr_url=settings.publication_url)
            return outcome
        finally:
            # Stop charging task time before the final API notification and auth cleanup.
            DEADLINE.reset(budget)
            try:
                usage = json.loads(settings.metrics_file.read_text(encoding="utf-8"))
                turns = usage.get("turns")
                turns = turns if type(turns) is int and turns >= 0 else "unknown"
                tokens = usage.get("tokens")
                tokens = tokens if type(tokens) is int and tokens >= 0 else "unknown"
            except (OSError, ValueError, TypeError):
                turns, tokens = "unknown", "unknown"
            detail = (" Refused: at most three starts are allowed per issue or pull request in one hour."
                      if outcome["result"] == "rate_limited" else
                      " The task timed out during publication; inspect the remote branch before dispatching again."
                      if outcome["result"] == "publication_unknown" else
                      " " + pause_description(settings.pause_reason) if outcome["result"] == "paused" else "")
            try:
                github.comment(task["number"], "<!-- soloforge:result:v1 -->\n"
                               f"SoloForge result: {outcome['result']}.{detail}\n\n"
                               f"Rounds: {turns}. Tokens: {tokens}. Duration: {time.monotonic() - start:.1f} seconds.")
            finally:
                if settings.ledger_record is not None:
                    record = {**settings.ledger_record, "time": datetime.now(timezone.utc).isoformat(),
                              "result": outcome["result"], "duration_sec": round(time.monotonic() - start, 1),
                              "turns": turns if type(turns) is int else None, "tokens": tokens if type(tokens) is int else None}
                    try:
                        github.comment(settings.ledger_issue, "```json\n" + json.dumps(record, indent=2) + "\n```")
                    except DispatchError:
                        print("::warning::Caller quota ledger could not be updated.", file=sys.stderr)


def main():
    try:
        if os.environ.get("GITHUB_EVENT_NAME") != "issue_comment":
            raise DispatchError("The dispatch Action accepts issue_comment events only.")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        settings = Settings(repository=os.environ.get("SF_REPOSITORY", ""),
                            token=os.environ.get("SF_GITHUB_TOKEN", ""),
                            workdir=Path(os.environ["SF_WORKDIR"]),
                            triggers=json.loads(os.environ.get("SF_TRIGGERS", '{"/codex":"codex","/claude":"claude"}')),
                            associations=json.loads(os.environ.get("SF_ALLOWED_ASSOCIATIONS", '["OWNER"]')),
                            pre_task=os.environ.get("SF_PRE_TASK", ""), post_task=os.environ.get("SF_POST_TASK", ""),
                            auth_secret_token=os.environ.get("SF_AUTH_SECRET_TOKEN", ""),
                            timeout_minutes=float(os.environ.get("SF_TIMEOUT_MINUTES", "30")),
                            max_turns=int(os.environ.get("SF_MAX_TURNS", "21")),
                            max_tokens=int(os.environ.get("SF_MAX_TOKENS", "244000")))
        outcome = dispatch(event, settings)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                for key, value in outcome.items():
                    if "\n" in value or "\r" in value:
                        raise DispatchError("An output value contained a newline.")
                    stream.write(f"{key}={value}\n")
        print("SoloForge result: " + outcome["result"])
        return 0
    except (DispatchError, ValueError, KeyError, OSError) as error:
        print(str(error) if isinstance(error, DispatchError) else "Invalid dispatch configuration or event.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
