#!/usr/bin/env python3
"""Provider adapters and sanitized Codex quota reads; standard library only."""
import json
import math
import os
from collections import deque
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time


class AdapterError(Exception):
    """A fixed, public-safe error code without provider payloads."""


class ProviderInterrupted(AdapterError):
    """Forward parent cancellation through provider process-tree cleanup."""


def provider_env(provider):
    publication = {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                   "SF_GITHUB_TOKEN", "SF_AUTH_SECRET_TOKEN", "CODEX_AUTH_JSON"}
    env = {name: value for name, value in os.environ.items()
           if name.upper() not in publication
           and not name.upper().startswith(("INPUT_", "ACTIONS_", "GIT_CONFIG_", "GIT_ASKPASS", "SSH_ASKPASS"))}
    if provider == "codex":
        if env.get("OPENAI_API_KEY"):
            env["CODEX_API_KEY"] = env["OPENAI_API_KEY"]
        env.pop("OPENAI_API_KEY", None)
    if provider != "codex":
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_HOME"):
            env.pop(name, None)
    if provider != "claude":
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            env.pop(name, None)
    return env


def private_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
    path.chmod(0o600)


def quota_failure(log):
    """Inspect error diagnostics, never ordinary model output or echoed prompts."""
    errors = []
    for line in log.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            if re.search(r"^(?:error|failed)\b[: ]|^\S+\s+ERROR\b", line, re.I):
                errors.append(line)
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "error":
            errors.append(str(event.get("message", "")))
        elif event.get("type") == "turn.failed":
            error = event.get("error")
            errors.append(str(error.get("message", "")) if isinstance(error, dict) else str(error or ""))
    return bool(re.search(r"(?:usage|rate)[ _-]+limit|quota[ _-]+exceeded", "\n".join(errors), re.I))


def positive_limit(name, default):
    value = os.environ.get(name, str(default))
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise AdapterError("invalid_adapter_budget")
    return int(value)


def timeout_budget():
    try:
        minutes = float(os.environ.get("SF_TIMEOUT_MINUTES", "30"))
    except ValueError:
        raise AdapterError("invalid_adapter_budget") from None
    if not math.isfinite(minutes) or minutes <= 0:
        raise AdapterError("invalid_adapter_budget")
    return minutes * 60


def write_metrics(metrics):
    path = os.environ.get("SF_METRICS_FILE")
    if path:
        target = Path(path)
        temporary = target.with_suffix(target.suffix + ".tmp")
        private_write(temporary, json.dumps(metrics) + "\n")
        temporary.replace(target)


def write_pause_reason(reason):
    path = os.environ.get("SF_PAUSE_REASON_FILE")
    if path:
        private_write(path, reason + "\n")


def usage_tokens(usage, provider):
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "output_tokens")
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in keys):
        return None
    total = sum(usage[key] for key in keys)
    if provider == "claude":
        # Anthropic reports cached input separately; Codex includes it in input.
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            value = usage.get(key, 0)
            if type(value) is not int or value < 0:
                return None
            total += value
    return total


def observe_event(event, provider, metrics, seen, usage_by_message):
    """Count completed Codex items or Claude assistant messages and reported usage."""
    if not isinstance(event, dict):
        return
    kind = event.get("type")
    if provider == "codex":
        if kind == "item.completed" and isinstance(event.get("item"), dict):
            identifier = event["item"].get("id")
            if not isinstance(identifier, str) or identifier not in seen:
                if isinstance(identifier, str):
                    seen.add(identifier)
                metrics["turns"] += 1
        if kind == "turn.completed":
            count = usage_tokens(event.get("usage"), provider)
            if count is not None:
                metrics["tokens"] = (metrics["tokens"] or 0) + count
    elif kind == "assistant" and isinstance(event.get("message"), dict):
        message = event["message"]
        identifier = message.get("id")
        if not isinstance(identifier, str):
            identifier = "anonymous-" + str(metrics["turns"])
        if identifier not in seen:
            seen.add(identifier)
            metrics["turns"] += 1
        count = usage_tokens(message.get("usage"), provider)
        if count is not None:
            usage_by_message[identifier] = max(usage_by_message.get(identifier, 0), count)
            metrics["tokens"] = sum(usage_by_message.values())
    elif kind == "result" or "num_turns" in event:
        turns = event.get("num_turns")
        if type(turns) is int and turns >= 0:
            metrics["turns"] = max(metrics["turns"], turns)
        count = usage_tokens(event.get("usage"), provider)
        if count is not None:
            metrics["tokens"] = max(metrics["tokens"] or 0, count)


def supervise_provider(command, *, prompt, cwd, env, provider, max_turns, max_tokens, timeout_seconds):
    """Stop one provider process tree at a deadline or observed execution budget."""
    metrics = {"turns": 0, "tokens": None}
    write_metrics(metrics)
    log_path = Path(os.environ["SF_LOG_FILE"])
    private_write(log_path, "")
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=cwd, env=env,
                            start_new_session=os.name == "posix")
    messages = queue.Queue()
    readers = []

    def read_lines(name, stream):
        try:
            while True:
                line = stream.readline(1048577)
                messages.put((name, line))
                if not line or len(line) > 1048576:
                    return
        except (OSError, ValueError):
            messages.put((name, b""))

    for name in ("stdout", "stderr"):
        thread = threading.Thread(target=read_lines, args=(name, getattr(proc, name)), daemon=True)
        thread.start()
        readers.append(thread)

    def send_prompt():
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    writer = threading.Thread(target=send_prompt, daemon=True)
    writer.start()
    output = {"stdout": deque(maxlen=200), "stderr": deque(maxlen=200)}
    deadline = time.monotonic() + timeout_seconds
    seen, usage_by_message = set(), {}
    exhausted, streams = False, 2
    previous_handler = None
    if os.name == "posix" and threading.current_thread() is threading.main_thread():
        previous_handler = signal.getsignal(signal.SIGTERM)

        def interrupted(signum, frame):
            raise ProviderInterrupted("adapter_cancelled")

        signal.signal(signal.SIGTERM, interrupted)
    try:
        with log_path.open("a", encoding="utf-8") as log:
            while streams or proc.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    exhausted = True
                    log.write("SoloForge budget exhausted: timeout\n")
                    break
                try:
                    name, raw = messages.get(timeout=min(remaining, .2))
                except queue.Empty:
                    if proc.poll() is not None:
                        break
                    continue
                if not raw:
                    streams -= 1
                    continue
                line = raw.decode("utf-8", errors="replace")
                output[name].append(line)
                log.write(line)
                log.flush()
                if len(raw) > 1048576:
                    exhausted = True
                    log.write("SoloForge budget exhausted: event_size\n")
                    break
                if name == "stdout":
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    observe_event(event, provider, metrics, seen, usage_by_message)
                    write_metrics(metrics)
                    if metrics["turns"] >= max_turns or (
                            metrics["tokens"] is not None and metrics["tokens"] >= max_tokens):
                        exhausted = True
                        log.write("SoloForge budget exhausted: observed_limit\n")
                        break
            code = 75 if exhausted else proc.wait()
    except ProviderInterrupted:
        exhausted, code = True, 75
    finally:
        try:
            stop_process(proc)
            for thread in readers:
                thread.join(timeout=.2)
            writer.join(timeout=.2)
            write_metrics(metrics)
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)
    result = subprocess.CompletedProcess(command, code, "".join(output["stdout"]), "".join(output["stderr"]))
    result.budget_exhausted = exhausted
    result.log_written = True
    return result


def run_adapter(provider):
    required = ("SF_INSTRUCTION_FILE", "SF_WORKDIR", "SF_SUMMARY_FILE", "SF_LOG_FILE")
    if any(not os.environ.get(name) for name in required):
        raise AdapterError("missing_adapter_input")
    instruction = Path(os.environ["SF_INSTRUCTION_FILE"]).resolve()
    workdir = Path(os.environ["SF_WORKDIR"]).resolve()
    summary = Path(os.environ["SF_SUMMARY_FILE"]).resolve()
    log = Path(os.environ["SF_LOG_FILE"]).resolve()
    if not instruction.is_file() or not workdir.is_dir():
        raise AdapterError("invalid_adapter_path")
    model = os.environ.get("SF_MODEL", "")
    effort = os.environ.get("SF_EFFORT", "")
    max_turns = positive_limit("SF_MAX_TURNS", 21)
    max_tokens = positive_limit("SF_MAX_TOKENS", 244000)
    timeout_seconds = timeout_budget()
    prompt = instruction.read_text(encoding="utf-8")
    if provider == "echo":
        code = int(os.environ.get("SF_ECHO_EXIT", "0"))
        private_write(log, f"Echo adapter exit: {code}\n")
        write_metrics({"turns": 0 if code else 1, "tokens": 0})
        if code == 75:
            write_pause_reason("budget" if os.environ.get("SF_ECHO_PAUSE_REASON") == "budget" else "quota")
        if code:
            return code
        output = workdir / ".soloforge/echo.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(prompt, encoding="utf-8")
        private_write(summary, "Echo adapter completed the requested test task.\n")
        return 0

    env = provider_env(provider)
    if provider == "codex":
        command = ["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
                   "--output-last-message", str(summary)]
        if model:
            command += ["--model", model]
        if effort:
            command += ["--config", "model_reasoning_effort=" + json.dumps(effort)]
        command.append("-")
    elif provider == "claude":
        if not env.get("ANTHROPIC_API_KEY"):
            raise AdapterError("missing_claude_credentials")
        command = ["claude", "-p", "--output-format", "stream-json", "--verbose",
                   "--max-turns", str(max_turns), "--dangerously-skip-permissions"]
        if model:
            command += ["--model", model]
        if effort:
            command += ["--effort", effort]
    else:
        raise AdapterError("unknown_adapter")

    result = supervise_provider(command, prompt=prompt, cwd=workdir, env=env, provider=provider,
                                max_turns=max_turns, max_tokens=max_tokens, timeout_seconds=timeout_seconds)
    if not getattr(result, "log_written", False):
        private_write(log, result.stdout + ("\n" + result.stderr if result.stderr else ""))
    if getattr(result, "budget_exhausted", False):
        write_pause_reason("budget")
        return 75
    if provider == "codex":
        if result.returncode == 0:
            if not summary.exists():
                private_write(summary, "Codex completed without a final summary.\n")
            return 0
        if quota_failure(result.stdout + "\n" + result.stderr):
            write_pause_reason("quota")
            return 75
        return 1

    payload = None
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (event.get("type") == "result" or "result" in event):
            payload = event
    if payload is None:
        return 1
    if payload.get("subtype") in ("error_max_turns", "error_max_budget_usd") or payload.get("terminal_reason") == "max_turns":
        write_pause_reason("budget")
        return 75
    if result.returncode or payload.get("is_error"):
        diagnostics = " ".join(str(payload.get(key, "")) for key in ("subtype", "error", "errors", "result"))
        if re.search(r"error_max_budget", diagnostics, re.I):
            write_pause_reason("budget")
            return 75
        if re.search(r"(?:usage|rate)[ _-]+limit|quota[ _-]+exceeded", diagnostics, re.I):
            write_pause_reason("quota")
            return 75
        return 1
    if not isinstance(payload.get("result"), str):
        return 1
    private_write(summary, payload["result"].rstrip() + "\n")
    return 0


def parse_rate_limits(data):
    """Normalize both protocol spellings and discard account identifiers."""
    result = {"five_hour": None, "weekly": None, "reason": None}
    data = data if isinstance(data, dict) else {}
    limits = data.get("rateLimits", data.get("rate_limits", data)) or {}
    if not isinstance(limits, dict):
        limits = {}
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        minutes = window.get("windowDurationMins", window.get("window_minutes"))
        name = None
        if type(minutes) in (int, float):
            if 60 <= minutes <= 720:
                name = "five_hour"
            elif 5000 <= minutes <= 20000:
                name = "weekly"
        used = window.get("usedPercent", window.get("used_percent"))
        if name is None or type(used) not in (int, float) or not 0 <= used <= 100:
            continue
        reset = window.get("resetsAt", window.get("resets_at"))
        if type(reset) not in (int, float) or not 0 <= reset <= 253402300799:
            reset = None
        result[name] = {"used_percent": used, "resets_at": reset}
    if result["weekly"] is None:
        result["reason"] = "missing_or_invalid_quota_windows"
    elif result["five_hour"] is None:
        result["reason"] = "five_hour_absent"
    return result


def stop_process(proc):
    if os.name == "posix":
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
            if sig == signal.SIGTERM:
                try:
                    proc.wait(timeout=.3)
                except subprocess.TimeoutExpired:
                    pass
    elif proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if proc.poll() is None:
            proc.kill()
    proc.wait()
    proc.stdin.close()
    proc.stdout.close()
    if proc.stderr is not None:
        proc.stderr.close()


def read_rpc(command=None, timeout=30):
    """One deadline covers the handshake and response, including partial lines."""
    deadline = time.monotonic() + timeout
    try:
        proc = subprocess.Popen(command or ["codex", "app-server"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=provider_env("codex"), start_new_session=os.name == "posix")
    except OSError:
        raise AdapterError("rpc_start_failed") from None
    messages = queue.Queue()

    def read_lines():
        try:
            while True:
                line = proc.stdout.readline(1048577)
                messages.put(line)
                if not line or len(line) > 1048576:
                    return
        except (OSError, ValueError):
            messages.put(b"")

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()
    previous_handler = None
    if os.name == "posix" and threading.current_thread() is threading.main_thread():
        previous_handler = signal.getsignal(signal.SIGTERM)

        def interrupted(signum, frame):
            raise ProviderInterrupted("quota_cancelled")

        signal.signal(signal.SIGTERM, interrupted)
    try:
        def send(message):
            try:
                proc.stdin.write((json.dumps(message) + "\n").encode())
                proc.stdin.flush()
            except OSError:
                raise AdapterError("rpc_write_failed") from None

        def receive(request_id):
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AdapterError("rpc_timeout")
                try:
                    line = messages.get(timeout=remaining)
                except queue.Empty:
                    raise AdapterError("rpc_timeout") from None
                if not line:
                    raise AdapterError("rpc_eof")
                if len(line) > 1048576:
                    raise AdapterError("rpc_invalid_json")
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    raise AdapterError("rpc_invalid_json") from None
                if not isinstance(message, dict) or message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise AdapterError("rpc_error")
                return message.get("result")

        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "soloforge-quota", "version": "1"}}})
        receive(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        return receive(2)
    finally:
        try:
            stop_process(proc)
            reader.join(timeout=.2)
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)


def read_quota():
    try:
        return parse_rate_limits(read_rpc())
    except AdapterError as exc:
        reason = str(exc)
        if reason not in {"rpc_start_failed", "rpc_write_failed", "rpc_timeout", "rpc_eof", "rpc_invalid_json", "rpc_error"}:
            reason = "quota_read_failed"
        return {"five_hour": None, "weekly": None, "reason": reason}
    except Exception:
        # Quota is optional telemetry. Never expose unexpected error details.
        return {"five_hour": None, "weekly": None, "reason": "quota_read_failed"}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("codex", "claude", "echo", "quota"):
        print("Usage: runner.py codex|claude|echo|quota", file=sys.stderr)
        return 2
    if sys.argv[1] == "quota":
        print(json.dumps(read_quota()))
        return 0
    try:
        return run_adapter(sys.argv[1])
    except AdapterError as exc:
        print(f"Adapter failed: {exc}", file=sys.stderr)
    except (OSError, ValueError):
        print("Adapter failed: local_execution_error", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
