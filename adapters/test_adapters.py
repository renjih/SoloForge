"""Adapter contracts, quota normalization and isolated RPC tests."""
import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("adapter_runner", ROOT / "runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.instruction = self.root / "instruction.md"
        self.instruction.write_text("Implement the example-game task.\n", encoding="utf-8")
        self.env = {
            "SF_WORKDIR": str(self.work), "SF_INSTRUCTION_FILE": str(self.instruction),
            "SF_SUMMARY_FILE": str(self.root / "summary.md"), "SF_LOG_FILE": str(self.root / "run.log"),
            "SF_PAUSE_REASON_FILE": str(self.root / "pause-reason.txt"),
            "SF_MODEL": "", "SF_EFFORT": "high", "OPENAI_API_KEY": "example-key",
            "ANTHROPIC_API_KEY": "example-key", "GH_TOKEN": "publication-token",
            "GITHUB_TOKEN": "publication-token", "CODEX_AUTH_JSON": "private-login-data",
        }

    def invoke(self, adapter, result):
        with patch.dict(os.environ, self.env), patch.object(runner, "supervise_provider", return_value=result) as run:
            code = runner.run_adapter(adapter)
        return code, run

    def test_echo_writes_instruction_and_summary(self):
        with patch.dict(os.environ, self.env):
            self.assertEqual(runner.run_adapter("echo"), 0)
        self.assertEqual((self.work / ".soloforge/echo.txt").read_text(), self.instruction.read_text())
        self.assertIn("Echo adapter", Path(self.env["SF_SUMMARY_FILE"]).read_text())

    def test_echo_pause_does_not_modify_worktree(self):
        with patch.dict(os.environ, {**self.env, "SF_ECHO_EXIT": "75"}):
            self.assertEqual(runner.run_adapter("echo"), 75)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_provider_environment_removes_publication_and_actions_credentials(self):
        publication = {
            "SF_GITHUB_TOKEN": "publication-token", "SF_AUTH_SECRET_TOKEN": "secret-write-token",
            "GH_ENTERPRISE_TOKEN": "enterprise-token", "GITHUB_ENTERPRISE_TOKEN": "enterprise-token",
            "INPUT_TOKEN": "input-token", "INPUT_GITHUB_TOKEN": "input-token",
            "INPUT_CODEX_AUTH_JSON": "raw-login", "ACTIONS_RUNTIME_TOKEN": "runtime-token",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "identity-token", "ACTIONS_RESULTS_URL": "private-service",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_VALUE_0": "credential-header", "GIT_ASKPASS": "credential-helper",
        }
        with patch.dict(os.environ, {**self.env, **publication}):
            for provider in ("codex", "claude", "echo"):
                env = runner.provider_env(provider)
                for key in publication:
                    self.assertNotIn(key, set(env), provider + ": " + key)
                for key in ("GH_TOKEN", "GITHUB_TOKEN", "CODEX_AUTH_JSON"):
                    self.assertNotIn(key, set(env), provider + ": " + key)

    def test_provider_credentials_are_isolated(self):
        extra = {"CODEX_ACCESS_TOKEN": "example-access-token", "CODEX_HOME": "private-codex-state",
                 "ANTHROPIC_AUTH_TOKEN": "example-auth-token", "CLAUDE_CODE_OAUTH_TOKEN": "example-oauth-token"}
        with patch.dict(os.environ, {**self.env, **extra}):
            codex = runner.provider_env("codex")
            for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
                self.assertNotIn(key, set(codex))
            self.assertEqual(codex["CODEX_API_KEY"], "example-key")
            self.assertEqual(codex["CODEX_HOME"], "private-codex-state")
            claude = runner.provider_env("claude")
            for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_HOME"):
                self.assertNotIn(key, set(claude))
            self.assertEqual(claude["ANTHROPIC_API_KEY"], "example-key")
            echo = runner.provider_env("echo")
            for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_HOME",
                        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
                self.assertNotIn(key, set(echo))

    def test_echo_does_not_invoke_a_provider(self):
        with patch.dict(os.environ, self.env), patch.object(runner.subprocess, "run") as run:
            self.assertEqual(runner.run_adapter("echo"), 0)
        run.assert_not_called()

    def test_codex_omits_empty_model_and_passes_prompt_over_stdin(self):
        code, run = self.invoke("codex", subprocess.CompletedProcess([], 0, "", ""))
        self.assertEqual(code, 0)
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["codex", "exec"])
        self.assertNotIn("--model", args)
        self.assertEqual(args[-1], "-")
        self.assertEqual(run.call_args.kwargs["prompt"], self.instruction.read_text())
        self.assertEqual(run.call_args.kwargs["cwd"], self.work)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["CODEX_API_KEY"], "example-key")
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "CODEX_AUTH_JSON", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(key, set(env))

    def test_codex_model_and_effort_are_single_arguments(self):
        self.env["SF_MODEL"] = "example-model"
        code, run = self.invoke("codex", subprocess.CompletedProcess([], 0, "", ""))
        self.assertEqual(code, 0)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--model") + 1], "example-model")
        self.assertIn('model_reasoning_effort="high"', args)

    def test_codex_rate_limit_is_pause_and_other_failures_are_failure(self):
        for message, expected in (("usage limit reached", 75), ("network timeout", 1)):
            with self.subTest(message=message):
                output = json.dumps({"type": "turn.failed", "error": {"message": message}})
                code, _ = self.invoke("codex", subprocess.CompletedProcess([], 1, output, ""))
                self.assertEqual(code, expected)

    def test_prompt_and_tool_output_cannot_fake_quota_diagnostics(self):
        for output in (
            "The instruction says usage limit reached",
            json.dumps({"type": "item.completed", "item": {"text": "usage limit reached"}}),
        ):
            self.assertFalse(runner.quota_failure(output))
        self.assertTrue(runner.quota_failure("ERROR: rate limit reached"))

    def test_claude_parses_summary_and_error_flag(self):
        code, run = self.invoke("claude", subprocess.CompletedProcess([], 0, json.dumps({
            "result": "Implemented the requested changes.", "is_error": False,
        }), ""))
        self.assertEqual(code, 0)
        self.assertEqual(Path(self.env["SF_SUMMARY_FILE"]).read_text(), "Implemented the requested changes.\n")
        self.assertNotIn("OPENAI_API_KEY", set(run.call_args.kwargs["env"]))
        self.assertIn("--effort", run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--max-turns") + 1], "21")
        self.assertIn("stream-json", run.call_args.args[0])
        for payload, expected in (
            ({"is_error": True, "result": "rate limit reached"}, 75),
            ({"is_error": True, "result": "permission denied"}, 1),
        ):
            code, _ = self.invoke("claude", subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
            self.assertEqual(code, expected)

    def test_claude_rejects_invalid_json_even_with_zero_exit(self):
        code, _ = self.invoke("claude", subprocess.CompletedProcess([], 0, "not a JSON result", ""))
        self.assertEqual(code, 1)

    def test_native_claude_round_limit_returns_pause(self):
        for payload in (
            {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "Turn limit reached."},
            {"type": "result", "terminal_reason": "max_turns", "result": "Paused."},
        ):
            code, _ = self.invoke("claude", subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
            self.assertEqual(code, 75)
            self.assertEqual(Path(self.env["SF_PAUSE_REASON_FILE"]).read_text().strip(), "budget")

    def test_quota_and_supervisor_budget_pauses_have_distinct_reason_files(self):
        quota = subprocess.CompletedProcess([], 1, json.dumps({"type": "error", "message": "usage limit reached"}), "")
        self.assertEqual(self.invoke("codex", quota)[0], 75)
        self.assertEqual(Path(self.env["SF_PAUSE_REASON_FILE"]).read_text().strip(), "quota")
        budget = subprocess.CompletedProcess([], 75, "", "")
        budget.budget_exhausted = True
        self.assertEqual(self.invoke("codex", budget)[0], 75)
        self.assertEqual(Path(self.env["SF_PAUSE_REASON_FILE"]).read_text().strip(), "budget")

    def test_limits_are_configurable_and_invalid_budgets_fail_before_provider(self):
        self.env.update(SF_MAX_TURNS="3", SF_MAX_TOKENS="4000", SF_TIMEOUT_MINUTES="2")
        code, run = self.invoke("codex", subprocess.CompletedProcess([], 0, "", ""))
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["max_turns"], 3)
        self.assertEqual(run.call_args.kwargs["max_tokens"], 4000)
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 120)
        self.env["SF_TIMEOUT_MINUTES"] = ".001"
        code, run = self.invoke("codex", subprocess.CompletedProcess([], 0, "", ""))
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], .06)
        self.env["SF_MAX_TURNS"] = "0"
        with self.assertRaisesRegex(runner.AdapterError, "invalid_adapter_budget"):
            self.invoke("codex", subprocess.CompletedProcess([], 0, "", ""))

    def test_provider_error_output_is_private_and_not_printed(self):
        output = json.dumps({"type": "turn.failed", "error": {"message": "private-response"}})
        with patch("builtins.print") as printed:
            code, _ = self.invoke("codex", subprocess.CompletedProcess([], 1, output, ""))
        self.assertEqual(code, 1)
        printed.assert_not_called()
        self.assertIn("private-response", Path(self.env["SF_LOG_FILE"]).read_text())

    def run_failing_cli(self, provider, exit_code, events=(), stdout="", stderr=""):
        path = self.root / "failure.json"
        stdout += "".join(json.dumps(event) + "\n" for event in events)
        script = (f"import sys\nsys.stdin.read()\nsys.stdout.write({stdout!r})\n"
                  f"sys.stderr.write({stderr!r})\nsys.exit({exit_code})\n")
        popen = subprocess.Popen

        def start_process(_command, **kwargs):
            return popen([sys.executable, "-u", "-c", script], **kwargs)

        with patch.dict(os.environ, {**self.env, "SF_FAILURE_FILE": str(path)}), \
                patch.object(runner.subprocess, "Popen", side_effect=start_process):
            return runner.run_adapter(provider), path

    def test_codex_failure_without_stdout_events_is_startup(self):
        code, path = self.run_failing_cli("codex", 1, stdout="Startup failed.\n",
                                         stderr='{"type":"error","message":"private stderr"}\n')
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(path.read_text()), {"kind": "startup", "codes": [], "status": None})

    def test_codex_failure_extracts_provider_code_and_http_status(self):
        body = {"error": {"code": "unsupported_value", "message": "private response"}}
        for event in (
            {"type": "error", "message": json.dumps({**body, "status": 400})},
            {"type": "turn.failed", "error": {
                "message": f"unexpected status 400 Bad Request: {json.dumps(body)}, request id: private-id",
            }},
        ):
            with self.subTest(event_type=event["type"]):
                code, path = self.run_failing_cli("codex", 1, events=[event])
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(path.read_text()), {
                    "kind": "provider", "codes": ["unsupported_value"], "status": 400,
                })

    def test_codex_failure_filters_codes_and_keeps_first_three_after_long_output(self):
        codes = ["has space", "Uppercase", "x" * 81, "first", "first", "second-code", "third.code_3", "fourth"]
        events = [{"type": "error", "message": json.dumps({"error": {"code": code}, "status": True})}
                  for code in codes]
        events.extend([{"type": "thread.started"}] * 210)
        code, path = self.run_failing_cli("codex", 1, events=events)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(path.read_text()), {
            "kind": "provider", "codes": ["first", "second-code", "third.code_3"], "status": None,
        })

    def test_codex_failure_with_ordinary_events_is_unknown(self):
        code, path = self.run_failing_cli("codex", 1, events=[{"type": "thread.started"}])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(path.read_text()), {"kind": "unknown", "codes": [], "status": None})

    def test_codex_pause_does_not_write_failure_file(self):
        reason = Path(self.env["SF_PAUSE_REASON_FILE"])
        for exit_code, message in ((75, "Provider paused."), (1, "rate limit reached"), (75, "rate limit reached")):
            with self.subTest(exit_code=exit_code, message=message):
                reason.unlink(missing_ok=True)
                code, path = self.run_failing_cli("codex", exit_code, events=[{"type": "error", "message": message}])
                self.assertEqual(code, 75)
                self.assertFalse(path.exists())
                if message == "rate limit reached":
                    self.assertEqual(reason.read_text(), "quota\n")

    def test_claude_failure_uses_result_subtype_without_result_text(self):
        payload = {"type": "result", "subtype": "error_during_execution", "is_error": True,
                   "result": "private provider response"}
        code, path = self.run_failing_cli("claude", 0, events=[payload])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(path.read_text()), {
            "kind": "provider", "codes": ["error_during_execution"], "status": None,
        })


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.metrics = self.root / "metrics.json"
        self.env = {"SF_LOG_FILE": str(self.root / "adapter.log"), "SF_METRICS_FILE": str(self.metrics)}

    def run_fake(self, script, provider="codex", turns=8, tokens=16000, timeout=5):
        processes, popen = [], subprocess.Popen

        def start_process(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch.dict(os.environ, self.env), patch.object(runner.subprocess, "Popen", side_effect=start_process):
            result = runner.supervise_provider([sys.executable, "-u", "-c", script], prompt="Example task.",
                                                cwd=self.root, env=runner.provider_env(provider), provider=provider,
                                                max_turns=turns, max_tokens=tokens, timeout_seconds=timeout)
        self.assertIsNotNone(processes[0].poll(), "Provider must exit or be stopped before returning")
        return result, json.loads(self.metrics.read_text())

    def test_runaway_codex_is_stopped_at_completed_item_budget(self):
        started = time.monotonic()
        result, metrics = self.run_fake('''
import json, sys, time
sys.stdin.read()
for number in range(20):
    print(json.dumps({'type': 'item.completed', 'item': {'id': str(number), 'type': 'command_execution'}}), flush=True)
time.sleep(30)
''', turns=2)
        self.assertEqual(result.returncode, 75)
        self.assertTrue(result.budget_exhausted)
        self.assertEqual(metrics, {"turns": 2, "tokens": None})
        self.assertLess(time.monotonic() - started, 5)

    def test_reported_codex_token_limit_stops_nonexiting_provider(self):
        result, metrics = self.run_fake('''
import json, sys, time
sys.stdin.read()
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 10, 'output_tokens': 3}}), flush=True)
time.sleep(30)
''', tokens=15)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(metrics, {"turns": 0, "tokens": 15})

    def test_success_reports_turns_and_usage_without_double_counting_cache(self):
        result, metrics = self.run_fake('''
import json, sys
sys.stdin.read()
print(json.dumps({'type': 'item.completed', 'item': {'id': 'one', 'type': 'agent_message'}}))
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 10, 'output_tokens': 3}}))
''')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(metrics, {"turns": 1, "tokens": 15})

    def test_claude_usage_includes_cache_and_result_does_not_double_count(self):
        result, metrics = self.run_fake('''
import json, sys
sys.stdin.read()
usage = {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 3, 'cache_creation_input_tokens': 2}
print(json.dumps({'type': 'assistant', 'message': {'id': 'one', 'usage': usage}}))
print(json.dumps({'type': 'result', 'num_turns': 1, 'usage': usage, 'result': 'Done.'}))
''', provider="claude")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(metrics, {"turns": 1, "tokens": 20})

    def test_duplicate_completed_items_are_counted_once(self):
        result, metrics = self.run_fake('''
import json, sys
sys.stdin.read()
event = {'type': 'item.completed', 'item': {'id': 'one', 'type': 'command_execution'}}
print(json.dumps(event))
print(json.dumps(event))
''', turns=2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(metrics, {"turns": 1, "tokens": None})

    def test_silent_provider_hits_deadline_and_reports_unknown_usage(self):
        result, metrics = self.run_fake("import sys,time;sys.stdin.read();time.sleep(30)", timeout=.2)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(metrics, {"turns": 0, "tokens": None})

    @unittest.skipUnless(sys.platform == "linux", "Linux SIGTERM process-group forwarding")
    def test_parent_sigterm_stops_detached_provider_and_descendant(self):
        provider_pid = self.root / "provider.pid"
        child_pid = self.root / "child.pid"
        child_script = f'''
import os, pathlib, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid()))
time.sleep(30)
'''
        provider_script = f'''
import os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(provider_pid)!r}).write_text(str(os.getpid()))
subprocess.Popen([sys.executable, '-u', '-c', {child_script!r}])
time.sleep(30)
'''
        supervisor_script = f'''
import os, pathlib, sys
sys.path.insert(0, {str(ROOT)!r})
import runner
os.environ.update({self.env!r})
result = runner.supervise_provider([sys.executable, '-u', '-c', {provider_script!r}],
    prompt='', cwd=pathlib.Path({str(self.root)!r}), env=runner.provider_env('codex'),
    provider='codex', max_turns=8, max_tokens=16000, timeout_seconds=30)
sys.exit(result.returncode)
'''
        parent = subprocess.Popen([sys.executable, "-u", "-c", supervisor_script],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while not (provider_pid.exists() and child_pid.exists()):
                if time.monotonic() >= deadline:
                    self.fail("Fake provider process tree did not start")
                time.sleep(.01)
            parent.send_signal(signal.SIGTERM)
            stdout, stderr = parent.communicate(timeout=3)
            self.assertEqual(parent.returncode, 75, stderr.decode(errors="replace"))
            self.assertEqual(json.loads(self.metrics.read_text()), {"turns": 0, "tokens": None})
            for file in (provider_pid, child_pid):
                pid = int(file.read_text())
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    status = Path(f"/proc/{pid}/stat")
                    if not status.exists() or status.read_text().split()[2] == "Z":
                        break
                    time.sleep(.01)
                else:
                    self.fail("Parent cancellation left a provider process running")
        finally:
            if parent.poll() is None:
                os.killpg(parent.pid, signal.SIGKILL)
                parent.communicate()
            if provider_pid.exists():
                try:
                    os.killpg(int(provider_pid.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


class QuotaTests(unittest.TestCase):
    def test_swapped_windows_and_snake_case(self):
        result = runner.parse_rate_limits({"rate_limits": {
            "primary": {"window_minutes": 10080, "used_percent": 20, "resets_at": 200},
            "secondary": {"window_minutes": 300, "used_percent": 40, "resets_at": 100},
            "accountId": "private-field",
        }})
        self.assertEqual(result["weekly"], {"used_percent": 20, "resets_at": 200})
        self.assertEqual(result["five_hour"], {"used_percent": 40, "resets_at": 100})
        self.assertNotIn("private-field", json.dumps(result))

    def test_window_duration_ranges_and_malformed_values(self):
        for minutes, expected in ((60, "five_hour"), (300, "five_hour"), (720, "five_hour"),
                                  (5000, "weekly"), (10080, "weekly"), (20000, "weekly"),
                                  (59, None), (721, None), (4999, None), (20001, None),
                                  ("300", None), (True, None)):
            with self.subTest(minutes=minutes):
                result = runner.parse_rate_limits({"primary": {"windowDurationMins": minutes, "usedPercent": 20}})
                for name in ("five_hour", "weekly"):
                    self.assertEqual(result[name], {"used_percent": 20, "resets_at": None} if name == expected else None)
        for raw in (None, {}, {"rateLimits": None}, {"primary": {}},
                    {"primary": {"windowDurationMins": 300, "usedPercent": True}},
                    {"primary": {"windowDurationMins": 300, "usedPercent": 101}}):
            with self.subTest(raw=raw):
                result = runner.parse_rate_limits(raw)
                self.assertIsNone(result["weekly"])
                self.assertIsNone(result["five_hour"])

    def test_weekly_only_snapshot_has_explicit_reason(self):
        result = runner.parse_rate_limits({"primary": {"windowDurationMins": 10080, "usedPercent": 30}})
        self.assertIsNone(result["five_hour"])
        self.assertEqual(result["reason"], "five_hour_absent")


class RpcTests(unittest.TestCase):
    def fake(self, code):
        return [sys.executable, "-u", "-c", code]

    def test_delayed_handshake_and_notifications(self):
        result = runner.read_rpc(self.fake('''
import json, os, time
assert 'GH_TOKEN' not in os.environ
assert 'GITHUB_TOKEN' not in os.environ
if os.name == 'posix':
    assert os.getsid(0) == os.getpid()
request = json.loads(input())
assert request['method'] == 'initialize'
time.sleep(.05)
print(json.dumps({'method': 'notification'}))
print(json.dumps({'id': request['id'], 'result': {}}))
assert json.loads(input())['method'] == 'initialized'
request = json.loads(input())
assert request['method'] == 'account/rateLimits/read'
print(json.dumps({'id': request['id'], 'result': {'rateLimits': {}}}))
time.sleep(30)
'''), timeout=3)
        self.assertEqual(result, {"rateLimits": {}})

    def test_partial_line_uses_total_deadline_and_kills_child(self):
        processes = []
        popen = subprocess.Popen

        def start_process(*args, **kwargs):
            proc = popen(*args, **kwargs)
            processes.append(proc)
            return proc

        start = time.monotonic()
        with patch.object(runner.subprocess, "Popen", side_effect=start_process):
            with self.assertRaisesRegex(runner.AdapterError, "rpc_timeout"):
                runner.read_rpc(self.fake("import sys,time;sys.stdout.write('{');sys.stdout.flush();time.sleep(30)"), timeout=.2)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIsNotNone(processes[0].poll(), "Timed-out app-server must be stopped")

    def test_deadline_is_shared_by_handshake_and_read(self):
        with self.assertRaisesRegex(runner.AdapterError, "rpc_timeout"):
            runner.read_rpc(self.fake('''
import json, time
request = json.loads(input())
time.sleep(.1)
print(json.dumps({'id': request['id'], 'result': {}}))
input()
request = json.loads(input())
time.sleep(.4)
print(json.dumps({'id': request['id'], 'result': {}}))
'''), timeout=.3)

    def test_rpc_error_eof_and_invalid_json_never_leak_response(self):
        for script in ("print('private-response')", "pass", '''
import json
request = json.loads(input())
print(json.dumps({'id': request['id'], 'error': {'message': 'private-response'}}))
'''):
            with self.subTest(script=script), self.assertRaises(runner.AdapterError) as caught:
                runner.read_rpc(self.fake(script), timeout=2)
            self.assertNotIn("private-response", str(caught.exception))

    def test_quota_read_error_outputs_only_fixed_reason(self):
        with patch.object(runner, "read_rpc", side_effect=runner.AdapterError("rpc_error")):
            self.assertEqual(runner.read_quota(), {"five_hour": None, "weekly": None, "reason": "rpc_error"})

    def test_unexpected_quota_error_is_unknown_without_details(self):
        for error in (ValueError("private-response"), runner.AdapterError("private-response")):
            with patch.object(runner, "read_rpc", side_effect=error):
                self.assertEqual(runner.read_quota(), {"five_hour": None, "weekly": None, "reason": "quota_read_failed"})

    @unittest.skipUnless(sys.platform == "linux", "Linux process-group cleanup")
    def test_cleanup_stops_descendants_after_server_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "descendant"
            child = f'''
import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open({str(pid_file)!r}, 'w').write(str(os.getpid()))
time.sleep(30)
'''
            server = f'''
import pathlib, subprocess, sys, time
subprocess.Popen([sys.executable, '-c', {child!r}])
while not pathlib.Path({str(pid_file)!r}).exists():
    time.sleep(.01)
'''
            try:
                with self.assertRaisesRegex(runner.AdapterError, "rpc_timeout"):
                    runner.read_rpc(self.fake(server), timeout=.5)
                pid = int(pid_file.read_text())
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    status = Path(f"/proc/{pid}/stat")
                    if not status.exists() or status.read_text().split()[2] == "Z":
                        break
                    time.sleep(.01)
                else:
                    self.fail("app-server descendant survived cleanup")
            finally:
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), 9)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
