"""Exercise dispatch publication with the echo adapter and real local Git remotes."""

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dispatch", HERE / "dispatch.py")
dispatch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dispatch
spec.loader.exec_module(dispatch)
REPOSITORY = "example-maintainer/example-game"


def git(cwd, *args):
    response = subprocess.run(["git", "-c", "user.name=Example Maintainer", "-c",
                               "user.email=maintainer@example.invalid", *args], cwd=cwd,
                              text=True, encoding="utf-8", capture_output=True, env=dispatch.clean_env())
    if response.returncode:
        raise AssertionError(response.stderr)
    return response.stdout.strip()


def event(body="/echo Implement the task", association="OWNER", pr=False):
    issue = {"number": 7}
    if pr:
        issue["pull_request"] = {}
    return {"action": "created", "repository": {"full_name": REPOSITORY}, "issue": issue,
            "comment": {"id": 123, "body": body, "author_association": association}}


class FakeGitHub:
    def __init__(self, remote):
        self.remote = remote
        self.comments = []
        self.history = []
        self.prs = []
        self.head_repo = REPOSITORY
        self.default_branch = "main"
        self.pr_branch = "feature/demo-app"
        self.reads = 0
        self.permissions = {"example-maintainer": "admin", "example-writer": "write"}

    def repo_info(self):
        self.reads += 1
        return {"default_branch": self.default_branch}

    def pull_request(self, number):
        sha = git(self.remote, "rev-parse", "refs/heads/" + self.pr_branch)
        return {"state": "open", "head": {"ref": self.pr_branch, "sha": sha,
                "repo": {"full_name": self.head_repo}},
                "base": {"repo": {"full_name": REPOSITORY}},
                "html_url": "https://github.com/" + REPOSITORY + "/pull/7"}

    def branch_exists(self, branch):
        return bool(git(self.remote, "for-each-ref", "--format=%(refname)", "refs/heads/" + branch))

    def comment(self, number, body):
        self.comments.append((number, body))
        record = {"id": len(self.history) + 1, "body": body,
                  "user": {"id": 1, "login": "example-automation[bot]", "type": "Bot"},
                  "created_at": datetime.now(timezone.utc).isoformat()}
        self.history.append(record)
        return record

    def list_comments(self, number):
        return self.history.copy()

    def has_write_permission(self, login):
        return self.permissions.get(login) in ("write", "maintain", "admin")

    def retire_reservation(self, identifier):
        next(row for row in self.history if row["id"] == identifier)["body"] = "Reservation refused."

    def open_pr(self, branch, base, number, summary):
        self.prs.append({"branch": branch, "base": base, "number": number, "summary": summary})
        return "https://github.com/" + REPOSITORY + "/pull/8"


class DispatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dispatch-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.seed = self.root / "seed"
        git(self.root, "clone", str(self.remote), str(self.seed))
        (self.seed / "README.md").write_text("# Example game\n", encoding="utf-8")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "Initial example")
        git(self.seed, "push", "origin", "main")
        self.api = FakeGitHub(self.remote)
        self.settings = dispatch.Settings(REPOSITORY, "test-publication-token", self.root / "work",
                                          triggers={"/echo": "echo"})

    def run_dispatch(self, incoming=None):
        return dispatch.dispatch(incoming or event(), self.settings, self.api, str(self.remote))

    def make_pr(self):
        git(self.seed, "checkout", "-b", self.api.pr_branch)
        (self.seed / "feature.txt").write_text("existing work\n", encoding="utf-8")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "Existing feature")
        git(self.seed, "push", "origin", self.api.pr_branch)

    def test_issue_comment_creates_branch_and_pr_with_echo(self):
        baseline = git(self.remote, "rev-parse", "main")
        outcome = self.run_dispatch()
        self.assertEqual(outcome["result"], "done")
        self.assertEqual(self.api.prs[0]["branch"], outcome["branch"])
        self.assertEqual(self.api.prs[0]["base"], "main")
        self.assertTrue(git(self.remote, "show", outcome["branch"] + ":.soloforge/echo.txt"))
        self.assertEqual(git(self.remote, "rev-parse", "main"), baseline)
        self.assertNotIn(self.settings.token, (self.settings.workdir / ".git/config").read_text())

    def test_pr_comment_pushes_original_branch_without_new_pr(self):
        self.make_pr()
        previous = git(self.remote, "rev-parse", self.api.pr_branch)
        outcome = self.run_dispatch(event(pr=True))
        self.assertEqual(outcome["branch"], self.api.pr_branch)
        self.assertEqual(outcome["result"], "done")
        self.assertNotEqual(git(self.remote, "rev-parse", self.api.pr_branch), previous)
        self.assertEqual(self.api.prs, [])
        self.assertIn(".soloforge/echo.txt", git(self.remote, "ls-tree", "--name-only", "-r", self.api.pr_branch))

    def test_pr_hooks_and_models_only_come_from_the_default_branch(self):
        config_path = self.seed / ".soloforge/models.json"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({
            "hooks": {"pre_task": "touch trusted-pre", "post_task": "touch trusted-post"},
            "adapters": {"echo": {"tiers": {"rework": {"model": "trusted-model"}}}},
        }))
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "Configure trusted hooks")
        git(self.seed, "push", "origin", "main")
        self.make_pr()
        config_path.write_text(json.dumps({
            "hooks": {"pre_task": "touch untrusted-pre", "post_task": "touch untrusted-post"},
            "adapters": {"echo": {"tiers": {"rework": {"model": "untrusted-model"}}}},
        }))
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "Propose untrusted hook changes")
        git(self.seed, "push", "origin", self.api.pr_branch)
        with patch.object(dispatch, "select_route", wraps=dispatch.select_route) as route:
            self.assertEqual(self.run_dispatch(event(pr=True))["result"], "done")
        self.assertEqual(route.call_args.args[0]["adapters"]["echo"]["tiers"]["rework"]["model"], "trusted-model")
        self.assertTrue((self.settings.workdir / "trusted-pre").exists())
        self.assertTrue((self.settings.workdir / "trusted-post").exists())
        self.assertFalse((self.settings.workdir / "untrusted-pre").exists())
        self.assertFalse((self.settings.workdir / "untrusted-post").exists())

    def test_unlisted_commenter_cannot_create_checkout_or_read_api(self):
        self.assertEqual(self.run_dispatch(event(association="CONTRIBUTOR"))["result"], "ignored")
        self.assertEqual(self.api.reads, 0)
        self.assertFalse(self.settings.workdir.exists())

    def test_configured_association_is_respected(self):
        self.settings.associations = ["MEMBER"]
        self.assertEqual(self.run_dispatch(event(association="MEMBER"))["result"], "done")

    def test_fork_is_refused_before_checkout_hooks_or_adapter(self):
        self.make_pr()
        self.api.head_repo = "example-contributor/example-game"
        self.settings.pre_task = "touch hook-marker"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-provider-secret"}):
            outcome = self.run_dispatch(event(pr=True))
        self.assertEqual(outcome["result"], "refused")
        self.assertFalse(self.settings.workdir.exists())
        self.assertEqual(self.api.prs, [])
        self.assertTrue(any("fork" in body for _, body in self.api.comments))

    def test_exit_75_does_not_commit_or_push_even_with_changed_files(self):
        branches = git(self.remote, "for-each-ref", "--format=%(refname)")
        self.settings.pre_task = 'mkdir -p .soloforge; printf partial > .soloforge/echo.txt'
        with patch.dict(os.environ, {"SF_ECHO_EXIT": "75"}):
            outcome = self.run_dispatch()
        self.assertEqual(outcome["result"], "paused")
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)"), branches)
        self.assertEqual(git(self.settings.workdir, "rev-parse", "HEAD"), git(self.remote, "rev-parse", "main"))
        self.assertTrue((self.settings.workdir / ".soloforge/echo.txt").is_file())
        self.assertEqual(self.api.prs, [])
        self.assertIn("paused", self.api.comments[-1][1])

    def test_untrusted_pushurl_is_replaced_before_publication(self):
        diverted = self.root / "diverted.git"
        git(self.root, "init", "--bare", str(diverted))
        self.settings.pre_task = 'git config remote.origin.pushurl "$DIVERTED_REMOTE"'
        with patch.dict(os.environ, {"DIVERTED_REMOTE": str(diverted)}):
            outcome = self.run_dispatch()
        self.assertEqual(outcome["result"], "done")
        self.assertTrue(self.api.branch_exists(outcome["branch"]))
        self.assertEqual(git(diverted, "for-each-ref", "--format=%(refname)"), "")

    def test_refreshed_auth_is_saved_even_when_adapter_times_out(self):
        self.settings.triggers = {"/codex": "codex"}
        self.settings.auth_secret_token = "secret-writeback-token"
        real_execute = dispatch.execute

        def timeout(command, **kwargs):
            if any(str(item).replace("\\", "/").endswith("codex/quota") for item in command):
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if any(str(item).replace("\\", "/").endswith("codex/run") for item in command):
                auth = Path(kwargs["env"]["CODEX_HOME"]) / "auth.json"
                auth.write_text('{"refresh_token":"new-example-login"}')
                raise dispatch.DispatchError("A required command could not complete.")
            return real_execute(command, **kwargs)

        with patch.dict(os.environ, {"CODEX_AUTH_JSON": '{"refresh_token":"old-example-login"}', "OPENAI_API_KEY": ""}):
            with patch.object(dispatch, "execute", side_effect=timeout):
                with patch.object(dispatch, "write_auth_secret") as saved:
                    with self.assertRaises(dispatch.DispatchError):
                        self.run_dispatch(event("/codex Implement it"))
        self.assertEqual(saved.call_count, 1)
        self.assertEqual(saved.call_args.args[0].repository, REPOSITORY)
        self.assertEqual(self.api.prs, [])

    def test_hanging_adapter_is_killed_at_total_timeout_and_comments(self):
        action = self.root / "action"
        entry = action / "adapters/hang/run"
        entry.parent.mkdir(parents=True)
        entry.write_text('#!/usr/bin/env bash\nset -eu\ntouch "$SF_WORKDIR/hang-started"\npython3 -c "import time; time.sleep(60)"\n', encoding="utf-8")
        entry.chmod(0o755)
        defaults = action / "actions/dispatch/defaults/models.json"
        defaults.parent.mkdir(parents=True)
        config = json.loads((HERE / "defaults/models.json").read_text())
        config["adapters"]["hang"] = copy.deepcopy(config["adapters"]["echo"])
        defaults.write_text(json.dumps(config), encoding="utf-8")
        self.settings.action_root = action
        self.settings.triggers = {"/hang": "hang"}
        self.settings.timeout_minutes = .06
        started = time.monotonic()
        outcome = self.run_dispatch(event("/hang Run the test"))
        self.assertEqual(outcome["result"], "paused")
        self.assertLess(time.monotonic() - started, 12)
        self.assertTrue((self.settings.workdir / "hang-started").is_file(), "The fake adapter must actually start")
        self.assertEqual(self.api.prs, [])
        self.assertFalse(self.api.branch_exists("soloforge/issue-7-comment-123"))
        self.assertIn("Duration:", self.api.comments[-1][1])
        self.assertIn("Rounds:", self.api.comments[-1][1])

    def test_fourth_trigger_in_one_hour_is_refused_without_adapter(self):
        for index in range(4):
            self.settings.workdir = self.root / f"attempt-{index}"
            incoming = event()
            incoming["comment"]["id"] += index
            outcome = self.run_dispatch(incoming)
            self.assertEqual(outcome["result"], "done" if index < 3 else "rate_limited")
        self.assertEqual(len(self.api.prs), 3)
        self.assertFalse(self.settings.workdir.exists())
        self.assertIn("three starts", self.api.comments[-1][1])
        self.assertEqual(sum(row["body"].startswith(dispatch.ATTEMPT_MARKER) for row in self.api.history), 3)

    def test_outsider_markers_do_not_consume_the_owner_budget(self):
        for identifier in range(1, 4):
            self.api.history.append({"id": identifier, "body": dispatch.ATTEMPT_MARKER + "\nForged reservation.",
                                     "created_at": datetime.now(timezone.utc).isoformat(),
                                     "user": {"id": 50, "login": "example-outsider", "type": "User"}})
        self.assertEqual(self.run_dispatch()["result"], "done")
        self.assertEqual(len(self.api.prs), 1)

    def test_timeout_after_push_reports_uncertain_publication_without_retry(self):
        original = dispatch.Git.run
        def accepted_then_timed_out(instance, *args, **kwargs):
            response = original(instance, *args, **kwargs)
            if args[0] == "push":
                raise dispatch.DispatchTimeout("Dispatch stopped at its configured wall-time limit.")
            return response
        with patch.object(dispatch.Git, "run", accepted_then_timed_out):
            outcome = self.run_dispatch()
        self.assertEqual(outcome["result"], "publication_unknown")
        self.assertTrue(self.api.branch_exists(outcome["branch"]))
        self.assertIn("inspect the remote branch", self.api.comments[-1][1])

    def test_failure_does_not_publish(self):
        with patch.dict(os.environ, {"SF_ECHO_EXIT": "1"}):
            with self.assertRaisesRegex(dispatch.DispatchError, "adapter failed"):
                self.run_dispatch()
        self.assertEqual(self.api.prs, [])
        self.assertFalse(self.api.branch_exists("soloforge/issue-7-comment-123"))

    def test_pause_comments_distinguish_provider_quota_and_execution_budget(self):
        for reason, expected in (("quota", "Provider quota is insufficient"), ("budget", "execution budget limit")):
            self.settings.workdir = self.root / reason
            with patch.dict(os.environ, {"SF_ECHO_EXIT": "75", "SF_ECHO_PAUSE_REASON": reason}):
                self.assertEqual(self.run_dispatch()["result"], "paused")
            self.assertIn(expected, self.api.comments[-1][1])
        self.assertEqual(self.api.prs, [])

    def test_repeated_issue_comment_does_not_overwrite_its_branch(self):
        self.run_dispatch()
        self.settings.workdir = self.root / "retry"
        self.assertEqual(self.run_dispatch()["result"], "duplicate")
        self.assertFalse(self.settings.workdir.exists())
        self.assertEqual(len(self.api.prs), 1)

    def test_comment_shell_syntax_is_literal_instruction_data(self):
        outcome = self.run_dispatch(event('/echo $(touch comment-marker)\n`touch another-marker`'))
        self.assertEqual(outcome["result"], "done")
        self.assertFalse((self.settings.workdir / "comment-marker").exists())
        self.assertFalse((self.settings.workdir / "another-marker").exists())

    def test_post_hook_receives_pause_result_without_provider_credentials(self):
        self.settings.pre_task = 'test -z "${OPENAI_API_KEY:-}" && test -z "${ANTHROPIC_API_KEY:-}"'
        self.settings.post_task = 'printf "%s" "$SF_RESULT" > hook-result'
        with patch.dict(os.environ, {"SF_ECHO_EXIT": "75", "OPENAI_API_KEY": "test-provider-secret"}):
            self.assertEqual(self.run_dispatch()["result"], "paused")
        self.assertEqual((self.settings.workdir / "hook-result").read_text(), "paused")


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((HERE / "defaults/models.json").read_text())

    def test_all_tiers_and_whole_line_matching(self):
        for tier in dispatch.TIERS:
            route = dispatch.select_route(self.config, "echo", "Tier: " + tier, "issue")
            self.assertEqual(route["tier"], tier)
        self.assertEqual(dispatch.select_route(self.config, "echo", "Use Tier: ultra please", "issue")["tier"], "normal")
        self.assertEqual(dispatch.select_route(self.config, "echo", "Update it", "pr")["tier"], "rework")

    def test_default_budgets_are_about_three_times_the_recorded_real_run(self):
        record = json.loads((dispatch.ROOT / "docs/benchmarks/codex-small-task.json").read_text())
        settings = dispatch.Settings(REPOSITORY, "test-token", Path("unused"))
        self.assertTrue(record["check_passed"])
        self.assertEqual(record["total_reported_tokens"], record["input_tokens"] + record["output_tokens"])
        self.assertEqual(settings.max_turns, 3 * record["completed_items"])
        self.assertLessEqual(3 * record["total_reported_tokens"], settings.max_tokens)
        self.assertLess(settings.max_tokens, 3 * record["total_reported_tokens"] + 1000)

    def test_repository_override_merges_over_defaults(self):
        combined = dispatch.merge_config(copy.deepcopy(self.config), {"adapters": {"echo": {
            "tiers": {"normal": {"model": "example-model"}}}}})
        self.assertEqual(dispatch.select_route(combined, "echo", "Task", "issue")["model"], "example-model")
        self.assertIn("codex", combined["adapters"])

    def test_quota_precedence_and_thresholds(self):
        def decision(short=None, weekly=None, mode="issue", tier="heavy"):
            snapshot = {"five_hour": {"used_percent": short}, "weekly": {"used_percent": weekly}}
            return dispatch.quota_decision(self.config, snapshot, mode, tier)
        self.assertEqual(decision(99, 1), "pause")
        self.assertEqual(decision(0, 99, "pr"), "pause")
        self.assertEqual(decision(0, 90), "pause")
        self.assertEqual(decision(0, 90, "pr", "rework"), "run")
        self.assertEqual(decision(0, 75), "downgrade")
        self.assertEqual(decision(None, None), "run")
        self.assertEqual(decision(True, float("nan")), "run")
        self.config["quota"]["weekly_stop_remaining"] = 40
        with self.assertRaises(dispatch.DispatchError):
            decision(0, 0)

    def test_trigger_words_are_data_and_missing_owner_is_invalid(self):
        settings = dispatch.Settings(REPOSITORY, "test-token", Path("unused"), triggers={"/$(echo)": "echo"})
        settings.validate()
        self.assertEqual(dispatch.authorized_task(event("/$(echo) task"), settings)["adapter"], "echo")
        settings.repository = ""
        with self.assertRaises(dispatch.DispatchError):
            settings.validate()

    def test_adapter_env_has_no_publication_or_other_provider_keys(self):
        task = {"adapter": "claude", "number": 7, "comment_id": 123, "mode": "issue", "branch": "task"}
        settings = dispatch.Settings(REPOSITORY, "test-token", Path("unused"))
        injected = {key: "secret" for key in dispatch.PROVIDER_SECRETS + dispatch.PUBLICATION_SECRETS}
        with patch.dict(os.environ, injected):
            env = dispatch.adapter_environment(task, settings, Path("temporary"), {"model": "", "effort": "high", "tier": "normal"})
        for key in dispatch.PUBLICATION_SECRETS + ("OPENAI_API_KEY", "CODEX_AUTH_JSON", "CODEX_API_KEY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "secret")

    def test_auth_refresh_writeback_targets_only_the_caller_repository(self):
        settings = dispatch.Settings(REPOSITORY, "publication-token", Path("unused"), auth_secret_token="writeback-token")
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / "auth.json"
            auth.write_text('{"refresh_token":"refreshed-example"}')
            with patch.object(dispatch, "execute", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                dispatch.write_auth_secret(settings, auth)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--repo") + 1], REPOSITORY)
        self.assertEqual(run.call_args.kwargs["env"]["GH_TOKEN"], "writeback-token")
        self.assertNotIn("publication-token", " ".join(command))

    def test_command_timeout_has_fixed_error_without_child_output(self):
        with self.assertRaisesRegex(dispatch.DispatchError, "required command could not complete"):
            dispatch.execute([sys.executable, "-c", "import time; print('private-output'); time.sleep(30)"], timeout=.2)

    def test_rate_ledger_uses_server_time_and_ignores_completed_or_old_runs(self):
        now = datetime.now(timezone.utc)
        def row(identifier, minutes, marker=dispatch.ATTEMPT_MARKER):
            return {"id": identifier, "created_at": (now - timedelta(minutes=minutes)).isoformat(), "body": marker + "\nReserved"}
        rows = dispatch.attempt_rows([row(1, 61), row(2, 30), row(3, 0, "completion")], now, lambda comment: True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 2)

    def test_reservation_authors_need_write_permission_or_the_same_bot_identity(self):
        for role, login, kind, identifier, expected in (
            ("read", "example-outsider", "User", 50, True),
            ("triage", "example-outsider", "User", 50, True),
            ("write", "example-writer", "User", 50, False),
            ("maintain", "example-writer", "User", 50, False),
            ("admin", "example-writer", "User", 50, False),
            ("read", "example-other[bot]", "Bot", 50, True),
            ("read", "example-automation[bot]", "Bot", 1, False),
        ):
            api = FakeGitHub(Path("unused"))
            api.permissions[login] = role
            api.history = [{"id": index, "body": dispatch.ATTEMPT_MARKER + "\nReserved.",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "user": {"id": identifier, "login": login, "type": kind}}
                           for index in range(1, 4)]
            with self.subTest(role=role, kind=kind, login=login):
                self.assertEqual(dispatch.reserve_attempt(api, 7), expected)

    @unittest.skipUnless(os.name == "posix", "Linux process-group cleanup")
    def test_outer_deadline_stops_supervised_provider_in_another_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pidfile = root / "provider.pid"
            script = """
import importlib.util, os, pathlib, sys
spec = importlib.util.spec_from_file_location('runner', sys.argv[1])
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
os.environ['SF_LOG_FILE'] = sys.argv[3]
provider = 'import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)'
runner.supervise_provider([sys.executable, '-u', '-c', provider, sys.argv[2]],
    prompt='', cwd=pathlib.Path(sys.argv[2]).parent, env=os.environ.copy(),
    provider='codex', max_turns=8, max_tokens=16000, timeout_seconds=60)
"""
            budget = dispatch.DEADLINE.set(time.monotonic() + 1.2)
            try:
                with self.assertRaises(dispatch.DispatchTimeout):
                    dispatch.execute([sys.executable, "-c", script, str(dispatch.ROOT / "adapters/runner.py"),
                                      str(pidfile), str(root / "provider.log")])
            finally:
                dispatch.DEADLINE.reset(budget)
            self.assertTrue(pidfile.exists(), "The detached provider must actually start")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)


if __name__ == "__main__":
    unittest.main()
