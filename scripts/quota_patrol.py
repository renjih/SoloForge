#!/usr/bin/env python3
"""Summarize a caller-owned quota ledger and deduplicate notification proposals."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PUSHED_START, PUSHED_END = "<!-- pushed:start -->", "<!-- pushed:end -->"
STATUS_START, STATUS_END = "<!-- status:start -->", "<!-- status:end -->"
ALERT_LEVELS = ("low", "rework_only", "stopped")
LEVEL_TEXT = {
    "normal": "normal", "5h_wait": "waiting for the five-hour quota reset",
    "low": "weekly quota is low", "rework_only": "weekly quota permits PR rework only",
    "stopped": "weekly quota requires a pause", "unknown": "quota is unavailable",
}


def gh(endpoint, method=None, body=None, paginate=False):
    command = ["gh", "api", "--hostname", "github.com", endpoint]
    if method:
        command += ["--method", method]
    if body is not None:
        command += ["--input", "-"]
    if paginate:
        command += ["--paginate", "--slurp"]
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, encoding="utf-8", capture_output=True, timeout=60)
    if result.returncode:
        match = re.search(r"HTTP (\d{3})", result.stderr)
        raise RuntimeError("GitHub HTTP " + match[1] if match else "GitHub request failed")
    data = json.loads(result.stdout) if result.stdout.strip() else None
    return [item for page in data for item in page] if paginate else data


def parse_time(value):
    if not value:
        return None
    value = str(value).strip()
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?) (UTC|EST|EDT)", value)
    try:
        if match:
            form = "%Y-%m-%d %H:%M:%S" if match[1].count(":") == 2 else "%Y-%m-%d %H:%M"
            offset = {"UTC": 0, "EST": -5, "EDT": -4}[match[2]]
            return datetime.strptime(match[1], form).replace(tzinfo=timezone(timedelta(hours=offset)))
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def time_text(value, zone=timezone.utc):
    return value.astimezone(zone).strftime("%m-%d %H:%M %Z") if value else "unknown"


def section(body, start, end):
    if body.count(start) != 1 or body.count(end) != 1:
        return None
    first, last = body.index(start) + len(start), body.index(end)
    return body[first:last].strip() if last >= first else None


def parse_status(body):
    text = section(body or "", STATUS_START, STATUS_END)
    result = {"found": text is not None, "level": "unknown", "weekly_remaining": None,
              "weekly_reset": None, "read_at": None, "actions_low": False,
              "actions_ok": False, "actions_line": None}
    if not text:
        return result
    match = re.search(r"^level:\s*(\S+)", text, re.M)
    if match and match[1] in LEVEL_TEXT:
        result["level"] = match[1]
    match = re.search(r"Weekly quota: remaining\s*([\d.]+)%(?:; resets\s*(.+))?", text)
    if match:
        try:
            remaining = float(match[1])
            if math.isfinite(remaining) and 0 <= remaining <= 100:
                result["weekly_remaining"] = remaining
        except ValueError:
            pass
        result["weekly_reset"] = parse_time(match[2]) if match[2] else None
    match = re.search(r"^Read at:\s*(.+)$", text, re.M)
    if match:
        result["read_at"] = parse_time(match[1])
    match = re.search(r"^Actions this month:.*$", text, re.M)
    if match:
        result["actions_line"] = match[0]
        result["actions_low"] = "actions_low" in match[0]
        result["actions_ok"] = re.search(r"\),\s*normal(?:;|$)", match[0]) is not None
    return result


def parse_pushed(body):
    text = section(body or "", PUSHED_START, PUSHED_END) or ""
    def field(name):
        match = re.search(r"^" + re.escape(name) + r":\s*(.*)$", text, re.M)
        return match[1].strip() if match else ""
    tasks = field("Pushed tasks")
    return {"level": field("level") or "normal",
            "tasks": [key for key in re.split(r"[;,\s]+", tasks) if "@" in key] if tasks != "none" else [],
            "unknown_at": parse_time(field("Unknown reminder")),
            "actions": field("Actions reminder") if field("Actions reminder") not in ("", "none") else None}


def render_pushed(level, tasks, unknown_at, now, actions=None):
    return "\n".join([
        f"level: {level}", "Pushed tasks: " + ("; ".join(tasks) if tasks else "none"),
        "Unknown reminder: " + (unknown_at.astimezone(timezone.utc).isoformat(timespec="seconds") if unknown_at else "none"),
        "Actions reminder: " + (actions or "none"),
        "Pushed at: " + now.astimezone(timezone.utc).isoformat(timespec="seconds"),
    ])


def replace_pushed(body, text):
    body = body or ""
    if STATUS_START in body or STATUS_END in body:
        if body.count(STATUS_START) != 1 or body.count(STATUS_END) != 1 or section(body, STATUS_START, STATUS_END) is None:
            raise RuntimeError("Status markers must be unique and ordered")
    if PUSHED_START not in body and PUSHED_END not in body:
        return body.rstrip() + f"\n\n{PUSHED_START}\n{text}\n{PUSHED_END}\n"
    if body.count(PUSHED_START) != 1 or body.count(PUSHED_END) != 1:
        raise RuntimeError("Pushed markers must be unique")
    first, last = body.index(PUSHED_START) + len(PUSHED_START), body.index(PUSHED_END)
    status_first, status_last = body.find(STATUS_START), body.find(STATUS_END)
    if last < first or (status_first >= 0 and status_last >= 0 and not (last < status_first or first > status_last)):
        raise RuntimeError("Pushed and status sections must not overlap")
    return body[:first] + "\n" + text + "\n" + body[last:]


def parse_ledger(comments):
    records, skipped = [], 0
    for comment in comments:
        match = re.fullmatch(r"\s*```json\s*\n(.*?)\n```\s*", comment.get("body") or "", re.S)
        if not match:
            continue
        try:
            record = json.loads(match[1])
        except ValueError:
            skipped += 1
            continue
        if not isinstance(record, dict) or not isinstance(record.get("model"), str):
            skipped += 1
            continue
        record["_time"] = parse_time(record.get("time")) or parse_time(comment.get("created_at"))
        records.append(record)
    return records, skipped


def weekly_used(snapshot):
    window = snapshot.get("weekly") if isinstance(snapshot, dict) else None
    used = window.get("used_percent") if isinstance(window, dict) else None
    if type(used) in (int, float) and math.isfinite(used) and 0 <= used <= 100:
        return used, window.get("resets_at")
    return None


def quota_delta(record):
    before, after = weekly_used(record.get("quota_before")), weekly_used(record.get("quota_after"))
    if not before or not after or any(type(window[1]) not in (int, float)
                                     or not math.isfinite(window[1]) or not 0 <= window[1] <= 253402300799
                                     for window in (before, after)) or before[1] != after[1]:
        return None
    difference = after[0] - before[0]
    return difference if difference >= 0 else None


def status_from_records(status, records):
    timed = [record for record in records if record["_time"] and record.get("level") in LEVEL_TEXT
             and record.get("adapter") in (None, "codex")]
    if not timed:
        return status
    latest = max(timed, key=lambda record: record["_time"])
    if status["read_at"] and latest["_time"] <= status["read_at"]:
        return status
    after, before = latest.get("quota_after"), latest.get("quota_before")
    snapshot = after if weekly_used(after) else before if weekly_used(before) else after or before or {}
    weekly = weekly_used(snapshot)
    result = {**status, "found": True, "level": latest.get("level", "unknown"),
              "read_at": parse_time(snapshot.get("read_at")) if isinstance(snapshot, dict) else None,
              "weekly_remaining": 100 - weekly[0] if weekly else None, "weekly_reset": None}
    if result["level"] not in LEVEL_TEXT:
        result["level"] = "unknown"
    result["read_at"] = result["read_at"] or latest["_time"]
    if weekly and type(weekly[1]) in (int, float) and math.isfinite(weekly[1]) and 0 <= weekly[1] <= 253402300799:
        result["weekly_reset"] = datetime.fromtimestamp(weekly[1], timezone.utc)
    return result


def token_counts(record):
    tokens = record.get("tokens")
    if type(tokens) is int and tokens >= 0:
        return tokens, None
    if not isinstance(tokens, dict):
        return None
    values = [0 if tokens.get(key) is None else tokens[key] for key in ("input", "cached_input", "output")]
    if any(type(value) is not int or value < 0 for value in values) or values[1] > values[0]:
        return None
    incoming, cached, outgoing = values
    return incoming + outgoing, incoming - cached + outgoing


def model_label(record):
    model = record.get("model") or "provider-default"
    return f"{model} {record['effort']}" if record.get("effort") else model


def codex_stats(records, start, end):
    groups = defaultdict(lambda: {"runs": 0, "tokens": 0, "fresh": 0, "fresh_unknown": 0, "legacy_records": 0,
                                  "quota": 0.0, "missing": 0, "seconds": 0})
    for record in records:
        stamp = record["_time"]
        if not stamp or not start <= stamp <= end:
            continue
        group = groups[model_label(record)]
        group["runs"] += 1
        duration = record.get("duration_sec")
        if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
            group["seconds"] += duration
        tokens = token_counts(record)
        if tokens:
            group["tokens"] += tokens[0]
            if tokens[1] is None:
                group["fresh_unknown"] += 1
            else:
                group["fresh"] += tokens[1]
                group["legacy_records"] += 1
        delta = quota_delta(record)
        if delta is None:
            group["missing"] += 1
        else:
            group["quota"] += delta
    return dict(sorted(groups.items(), key=lambda pair: -pair[1]["tokens"]))


def norm_trigger(trigger):
    last = trigger.get("last_run") or {}
    derived = trigger.get("derived_state") or {}
    return {"id": trigger.get("id"), "name": trigger.get("name") or "",
            "model": trigger.get("model") or derived.get("model") or "provider-default",
            "status": (trigger.get("status") or last.get("status") or "").upper(),
            "fired_at": parse_time(trigger.get("fired_at") or last.get("fired_at")),
            "finished_at": parse_time(trigger.get("finished_at") or last.get("finished_at")),
            "enabled": trigger.get("enabled", True)}


def trigger_findings(triggers, now, self_name="Quota patrol"):
    findings = []
    for trigger in triggers:
        if trigger["name"] == self_name or not trigger["enabled"] or not trigger["fired_at"]:
            continue
        age = now - trigger["fired_at"]
        if "FAIL" in trigger["status"] and timedelta(0) <= age <= timedelta(hours=24):
            findings.append({**trigger, "kind": "failed"})
        elif any(word in trigger["status"] for word in ("PENDING", "RUNNING")) and not trigger["finished_at"] and age > timedelta(hours=2):
            findings.append({**trigger, "kind": "stuck"})
    return findings


def claude_stats(triggers, now):
    groups = defaultdict(lambda: {"tasks": 0, "minutes": 0.0})
    for trigger in triggers:
        if trigger["fired_at"] and timedelta(0) <= now - trigger["fired_at"] <= timedelta(hours=24):
            group = groups[trigger["model"]]
            group["tasks"] += 1
            if trigger["finished_at"] and trigger["finished_at"] >= trigger["fired_at"]:
                group["minutes"] += (trigger["finished_at"] - trigger["fired_at"]).total_seconds() / 60
    return dict(sorted(groups.items(), key=lambda pair: -pair[1]["minutes"]))


def decide(status, pushed, findings, now, issue_url="", zone=timezone.utc):
    level, alerts, lines = status["level"], [], []
    new_level, tasks, unknown_at = pushed["level"], list(pushed["tasks"]), pushed["unknown_at"]
    actions = pushed.get("actions")
    if level in ALERT_LEVELS and level != pushed["level"]:
        alerts.append("level")
        remaining = status["weekly_remaining"]
        lines.append(f"Quota {LEVEL_TEXT[level]}: weekly remaining {remaining:g}%; resets {time_text(status['weekly_reset'], zone)}."
                     if remaining is not None else f"Quota {LEVEL_TEXT[level]}.")
        new_level = level
    elif level == "normal" and pushed["level"] != "normal":
        new_level = "normal"
    if status["actions_low"] and not actions:
        alerts.append("actions_low")
        lines.append("GitHub Actions monthly usage is high: " + (status["actions_line"] or ""))
        actions = now.astimezone(zone).strftime("%Y-%m")
    elif status["actions_ok"] and actions:
        actions = None
    if findings is not None:
        current_keys = {f"{finding['id']}@{finding['fired_at'].isoformat(timespec='seconds')}" for finding in findings}
        tasks = [key for key in tasks if key in current_keys]
    fresh = [finding for finding in findings or [] if f"{finding['id']}@{finding['fired_at'].isoformat(timespec='seconds')}" not in tasks]
    if fresh:
        alerts.append("tasks")
        names = ", ".join(f"{finding['name']} {finding['kind']} ({time_text(finding['fired_at'], zone)})" for finding in fresh[:4])
        tip = "; check provider usage when multiple tasks fail together" if len(fresh) >= 2 else ""
        lines.append("Scheduled task problems: " + names + tip)
        tasks += [f"{finding['id']}@{finding['fired_at'].isoformat(timespec='seconds')}" for finding in fresh]
    stale = status["read_at"] is None or now - status["read_at"] > timedelta(days=7)
    if (level == "unknown" or stale) and (unknown_at is None or now - unknown_at > timedelta(days=7)):
        alerts.append("unknown")
        lines.append("Quota is unavailable or has not been updated for more than 7 days; inspect dispatcher authentication and runs.")
        unknown_at = now
    changed = ((new_level, tasks, unknown_at, actions) != (pushed["level"], pushed["tasks"], pushed["unknown_at"], pushed.get("actions")))
    message = "Quota patrol\n" + "\n".join(lines) + ("\n" + issue_url if issue_url else "") if lines else None
    return {"alerts": alerts, "message": message, "pushed_changed": changed,
            "pushed_text": render_pushed(new_level, tasks, unknown_at, now, actions) if changed else None}


def stats_lines(today, week, status, week_note, scheduled, records_total):
    lines = []
    if not records_total:
        lines.append("Dispatcher: no ledger records yet")
    else:
        for name in sorted(set(today) | set(week), key=lambda item: -week.get(item, {}).get("tokens", 0)):
            day = today.get(name) or {"runs": 0, "tokens": 0, "quota": 0}
            seven = week.get(name) or {"runs": 0, "tokens": 0, "quota": 0}
            line = (f"{name} | today {day['runs']} runs, {day['tokens']} reported tokens, {day['quota']:g}% weekly quota"
                    f" | this week {seven['runs']} runs, {seven['tokens']} reported tokens, {seven['quota']:g}% weekly quota")
            if seven.get("legacy_records"):
                line += f"; {seven['fresh']} legacy cache-excluded tokens this week"
            lines.append(line)
        missing = sum(group["missing"] for group in week.values())
        used = sum(group["quota"] for group in week.values())
        note = f"Recorded dispatch weekly quota: {used:g}%; integer metrics are reported totals; legacy cache-excluded counts are separate"
        if status["weekly_remaining"] is not None:
            note += f"; current weekly remaining {status['weekly_remaining']:g}%"
        if missing:
            note += f"; {missing} records lack a comparable quota window"
        if week_note:
            note += "; " + week_note
        lines.append(note)
    if scheduled:
        lines.append("Scheduled runs in the last 24 hours: " + ", ".join(
            f"{model} {group['tasks']} tasks/{group['minutes']:.0f} minutes" for model, group in scheduled.items()))
    else:
        lines.append("No scheduled runs observed in the last 24 hours")
    return lines


def report(body, comments, triggers_raw, now, issue_url="", zone=timezone.utc, self_name="Quota patrol"):
    records, skipped = parse_ledger(comments)
    status = status_from_records(parse_status(body), records)
    pushed = parse_pushed(body)
    triggers = [norm_trigger(item) for item in triggers_raw] if triggers_raw is not None else []
    findings = trigger_findings(triggers, now, self_name)
    day_start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = status["weekly_reset"] - timedelta(days=7) if status["weekly_reset"] else now - timedelta(days=7)
    week_note = "" if status["weekly_reset"] else "reset unavailable; week means the last 7 days"
    today, week = codex_stats(records, day_start, now), codex_stats(records, week_start, now)
    decision = decide(status, pushed, findings if triggers_raw is not None else None, now, issue_url, zone)
    lines = stats_lines(today, week, status, week_note, claude_stats(triggers, now), len(records))
    if triggers_raw is None:
        lines.append("Scheduled task input unavailable; scheduled failure checks skipped")
    daily = "Daily model usage\n" + "\n".join(lines if len(lines) <= 8 else lines[:5] + lines[-3:]) if now.astimezone(zone).hour >= 22 or now.astimezone(zone).hour < 1 else None
    return {"summary": f"Quota patrol: {status['level']}; {len(findings)} task findings; {1 if decision['message'] else 0} proposed alerts",
            "stats": lines, "push_message": decision["message"], "daily_message": daily,
            "alerts": decision["alerts"], "pushed_changed": decision["pushed_changed"], "pushed_text": decision["pushed_text"],
            "failed_tasks": [{"name": item["name"], "kind": item["kind"], "fired_at": item["fired_at"].isoformat()} for item in findings],
            "codex": {"level": status["level"], "weekly_remaining": status["weekly_remaining"],
                      "records": len(records), "unparsable": skipped, "status_found": status["found"]}}


def positive_integer(value):
    if not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise argparse.ArgumentTypeError("must be a positive integer")
    return int(value)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="Inspect a caller-owned quota ledger")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("report", "mark-pushed"):
        current = sub.add_parser(command)
        current.add_argument("--owner", default=os.environ.get("SOLOFORGE_OWNER"))
        current.add_argument("--repo", required=True, help="Repository name without the owner")
        current.add_argument("--ledger-issue", type=positive_integer, default=os.environ.get("SOLOFORGE_LEDGER_ISSUE"))
        if command == "report":
            current.add_argument("--triggers")
            current.add_argument("--issue-file")
            current.add_argument("--comments-file")
            current.add_argument("--now")
            current.add_argument("--timezone", default="UTC")
            current.add_argument("--self-name", default="Quota patrol")
            current.add_argument("--out", default="patrol.json")
            current.add_argument("--pushed-out", default="pushed.txt")
        else:
            current.add_argument("--pushed", required=True)
    args = parser.parse_args(argv)
    if not args.owner or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", args.owner):
        parser.error("--owner or SOLOFORGE_OWNER is required and must be an owner name")
    if args.repo in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be a repository name without an owner")
    if args.ledger_issue is None:
        parser.error("--ledger-issue or SOLOFORGE_LEDGER_ISSUE is required")
    return args


def main(argv=None):
    args = arguments(argv)
    endpoint = f"repos/{args.owner}/{args.repo}/issues/{args.ledger_issue}"
    if args.command == "mark-pushed":
        text = Path(args.pushed).read_text(encoding="utf-8").strip()
        if not text:
            print("Pushed-state input is empty; no update needed")
            return 0
        issue = gh(endpoint)
        body = issue.get("body") or ""
        updated = replace_pushed(body, text)
        if section(updated, STATUS_START, STATUS_END) != section(body, STATUS_START, STATUS_END):
            raise RuntimeError("Status section would change; update refused")
        gh(endpoint, method="PATCH", body={"body": updated})
        print("Pushed section updated")
        return 0
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("--now must be an ISO timestamp")
    try:
        zone = timezone.utc if args.timezone == "UTC" else ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        raise ValueError("Requested timezone is unavailable") from None
    errors = []
    try:
        issue = json.loads(Path(args.issue_file).read_text(encoding="utf-8")) if args.issue_file else gh(endpoint)
        body = issue.get("body") or ""
    except Exception:
        body = ""
        errors.append("Ledger issue unavailable")
    try:
        comments = json.loads(Path(args.comments_file).read_text(encoding="utf-8")) if args.comments_file else gh(endpoint + "/comments?per_page=100", paginate=True)
        if not isinstance(comments, list):
            raise ValueError
    except Exception:
        comments = []
        errors.append("Ledger records unavailable")
    triggers = None
    if args.triggers:
        try:
            raw = json.loads(Path(args.triggers).read_text(encoding="utf-8"))
            triggers = raw.get("data", raw) if isinstance(raw, dict) else raw
            if not isinstance(triggers, list):
                raise ValueError
        except Exception:
            triggers = None
            errors.append("Scheduled task input unavailable")
    output = report(body, comments, triggers, now, f"https://github.com/{args.owner}/{args.repo}/issues/{args.ledger_issue}", zone, args.self_name)
    output["errors"] = errors
    if "Ledger issue unavailable" in errors:
        output.update(push_message=None, daily_message=None, pushed_changed=False, pushed_text=None)
    Path(args.out).write_text(json.dumps(output, indent=2), encoding="utf-8")
    Path(args.pushed_out).write_text(output["pushed_text"] or "", encoding="utf-8")
    print(output["summary"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError):
        print("Quota patrol failed: invalid input or unavailable repository state", file=sys.stderr)
        sys.exit(1)
