#!/usr/bin/env python3
"""Read repository health through REST; one repository failure never hides others."""

from datetime import datetime, timezone
import json
import re
import subprocess


class Gh:
    def __init__(self, runner=None):
        self.runner = runner or self._run
        self.n = 0

    @staticmethod
    def _run(path):
        result = subprocess.run(["gh", "api", "--hostname", "github.com", path], capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        if result.returncode:
            raise ValueError("GitHub repository state could not be read")
        return json.loads(result.stdout)

    def __call__(self, path):
        self.n += 1
        return self.runner(path)


def minutes_since(now, stamp):
    return max(0, int((now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() // 60))


def light_and_line(row, now):
    parts = []
    dispatch = row.get("dispatcher") or {}
    waits = [item for item in row.get("waiting_merge") or [] if (item.get("minutes") or 0) > 60]
    if row.get("error"):
        parts.append("State could not be read")
    if row.get("ci") == "red":
        parts.append("Default branch CI is red")
    if dispatch.get("result"):
        parts.append("Dispatcher: " + dispatch["result"])
    if waits:
        parts.append(f"{len(waits)} PRs waiting for merge for more than an hour")
    if row.get("needs_owner"):
        parts.append(f"{len(row['needs_owner'])} items need the owner")
    if row.get("needs_author"):
        parts.append(f"{len(row['needs_author'])} items need the author")
    if row.get("needs_arch"):
        parts.append(f"{len(row['needs_arch'])} items need architecture review")
    if row.get("error") or row.get("ci") == "red" or dispatch.get("result") == "failed":
        light = "red"
    elif waits or row.get("needs_owner") or row.get("needs_author") or row.get("needs_arch") or dispatch.get("result") in ("paused", "publication_unknown"):
        light = "yellow"
    else:
        light = "green"
    return light, "; ".join(parts) or "No action needed"


def collect_repo(project, gh, now):
    repo = project["repo"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Health repositories require owner/repository names")
    prefix = f"repos/{repo}"
    errors = []

    def read(call, default=None):
        try:
            return call()
        except Exception:
            errors.append("GitHub read failed")
            return default

    metadata = read(lambda: gh(prefix), {})
    branch = metadata.get("default_branch", "main")
    runs = read(lambda: gh(f"{prefix}/actions/runs?branch={branch}&event=push&per_page=20"), {}).get("workflow_runs") or []
    runs = [run for run in runs if run.get("event") == "push"]
    if runs:
        runs = [run for run in runs if run.get("head_sha") == runs[0].get("head_sha")]
    states = {run.get("conclusion") or run.get("status") for run in runs}
    if states & {"failure", "timed_out", "startup_failure"}:
        ci = "red"
    elif states & {"in_progress", "queued", "waiting", "pending", "requested"}:
        ci = "running"
    elif "success" in states:
        ci = "green"
    else:
        ci = "absent"
    issues = read(lambda: gh(f"{prefix}/issues?state=open&per_page=100"), [])
    pulls = read(lambda: gh(f"{prefix}/pulls?state=open&per_page=100"), [])
    waits, comments = [], []
    for pull in sorted(pulls, key=lambda item: item["number"]):
        labels = {label["name"] for label in pull.get("labels") or []}
        eligible = labels & {"P2", "P3"}
        if eligible:
            events = read(lambda: gh(f"{prefix}/issues/{pull['number']}/events?per_page=100"), [])
            tagged = [event for event in events if event.get("event") == "labeled"
                      and event.get("label", {}).get("name") in eligible]
            latest = max(tagged, key=lambda item: item["created_at"]) if tagged else None
            waits.append({"pr": pull["number"], "minutes": minutes_since(now, latest["created_at"]) if latest else None})
    numbers = {pull["number"] for pull in pulls}
    if project.get("dispatch_issue"):
        numbers.add(project["dispatch_issue"])
    for number in sorted(numbers):
        issue = next((item for item in issues if item["number"] == number), None)
        if issue is None:
            issue = read(lambda: gh(f"{prefix}/issues/{number}"), {})
        count = issue.get("comments") or 0
        if count:
            page = (count + 99) // 100
            comments += read(lambda: gh(f"{prefix}/issues/{number}/comments?per_page=100&page={page}"), [])
    results = []
    for comment in comments:
        match = re.search(r"^SoloForge result: ([a-z_]+)\.", comment.get("body") or "", re.M)
        if match:
            results.append({"at": comment["created_at"], "result": match[1], "id": comment.get("id", 0)})
    dispatcher = max(results, key=lambda item: (item["at"], item["id"])) if results else None
    closed = read(lambda: gh(f"{prefix}/pulls?state=closed&sort=updated&direction=desc&per_page=15"), [])
    merged = sorted([pull for pull in closed if pull.get("merged_at")], key=lambda item: item["merged_at"])
    items = {item["number"]: item for item in issues + pulls}.values()

    def labeled(label):
        return sorted({item["number"] for item in items if label in [entry["name"] for entry in item.get("labels") or []]})

    row = {"repo": repo, "name": project.get("name", repo), "ci": ci,
           "prs": {"total": len(pulls)}, "waiting_merge": waits,
           "needs_owner": labeled("needs-owner"), "needs_author": labeled("needs-author"),
           "needs_arch": labeled("needs-arch"), "dispatcher": dispatcher,
           "last_merge": {"pr": merged[-1]["number"], "at": merged[-1]["merged_at"]} if merged else None,
           "error": "; ".join(errors) or None}
    row["light"], row["line"] = light_and_line(row, now)
    return row


def collect(projects, gh=None, now=None, by="local"):
    if not isinstance(projects, list):
        raise ValueError("Health projects must be a list")
    gh = gh or Gh()
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    rows = [collect_repo(project, gh, now) for project in projects]
    return {"checked": now.isoformat(), "by": by, "total": len(rows), "requests": gh.n,
            "ok": sum(not row["error"] for row in rows), "repos": rows}
