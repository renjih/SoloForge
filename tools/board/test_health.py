#!/usr/bin/env python3
"""Offline repository-health contracts and an optional fake GitHub CLI."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import health


NOW = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)
PROJECTS = [
    {"repo": "example-owner/example-game", "name": "example-game", "dispatch_issue": 12},
    {"repo": "example-owner/demo-app", "name": "demo-app", "dispatch_issue": 1},
    {"repo": "another-owner/demo-app", "name": "demo-app (healthy)"},
]


def pr(number, labels=()):
    return {"number": number, "head": {"ref": f"codex/task-{number}"},
            "labels": [{"name": label} for label in labels], "title": f"PR {number}"}


def issue(number, labels=(), comments=0, is_pr=False):
    result = {"number": number, "title": f"Task {number}", "comments": comments,
              "labels": [{"name": label} for label in labels]}
    if is_pr:
        result["pull_request"] = {}
    return result


def comment(body, at, identifier=1):
    return {"body": body, "created_at": at, "id": identifier}


def world():
    """One red repository, one partially unreadable repository and one quiet one."""
    game = "repos/example-owner/example-game"
    app = "repos/example-owner/demo-app"
    quiet = "repos/another-owner/demo-app"
    return {
        game: {"default_branch": "main"},
        f"{game}/actions/runs?branch=main&event=push&per_page=20": {
            "workflow_runs": [{"event": "push", "head_sha": "game-head", "conclusion": "failure", "status": "completed"}]},
        f"{game}/issues?state=open&per_page=100": [
            issue(12, comments=2), issue(40, ["P3", "needs-author"], comments=1, is_pr=True),
            issue(41, ["needs-arch"], is_pr=True), issue(55, ["needs-owner"])],
        f"{game}/pulls?state=open&per_page=100": [
            pr(40, ["P3", "needs-author"]), pr(41, ["needs-arch"])],
        f"{game}/issues/12/comments?per_page=100&page=1": [
            comment("SoloForge result: failed.\nPrevious attempt.", "2026-11-19T19:00:00Z", 1),
            comment("An ordinary discussion comment.", "2026-11-19T19:50:00Z", 2)],
        f"{game}/issues/40/comments?per_page=100&page=1": [
            comment("SoloForge result: done.\nCurrent implementation.", "2026-11-19T19:30:00Z", 3)],
        f"{game}/issues/40/events?per_page=100": [
            {"event": "labeled", "label": {"name": "P3"}, "created_at": "2026-11-19T18:00:00Z"},
            {"event": "labeled", "label": {"name": "needs-author"}, "created_at": "2026-11-19T19:00:00Z"}],
        f"{game}/pulls?state=closed&sort=updated&direction=desc&per_page=15": [
            {"number": 39, "merged_at": "2026-11-18T01:00:00Z"}, {"number": 38, "merged_at": None}],
        app: {"default_branch": "main"},
        f"{app}/actions/runs?branch=main&event=push&per_page=20": {"workflow_runs": [{"event": "push", "head_sha": "app-head", "conclusion": "success"}]},
        f"{app}/issues?state=open&per_page=100": 403,
        f"{app}/pulls?state=open&per_page=100": [],
        f"{app}/issues/1": issue(1),
        f"{app}/pulls?state=closed&sort=updated&direction=desc&per_page=15": [],
        quiet: {"default_branch": "main"},
        f"{quiet}/actions/runs?branch=main&event=push&per_page=20": {"workflow_runs": []},
        f"{quiet}/issues?state=open&per_page=100": [],
        f"{quiet}/pulls?state=open&per_page=100": [],
        f"{quiet}/pulls?state=closed&sort=updated&direction=desc&per_page=15": [],
    }


def runner(responses, calls=None):
    def run(path):
        if calls is not None:
            calls.append(path)
        response = responses.get(path)
        if response is None or response == 403:
            raise RuntimeError("Private upstream diagnostics must not be published")
        return response
    return run


class PushCiTest(unittest.TestCase):
    def collect_runs(self, runs):
        responses, calls = world(), []
        game = "repos/example-owner/example-game"
        response = {"workflow_runs": runs}
        responses[f"{game}/actions/runs?branch=main&event=push&per_page=20"] = response
        result = health.collect([PROJECTS[0]], health.Gh(runner(responses, calls)), NOW)
        return result["repos"][0], calls

    def test_scheduled_success_does_not_hide_push_failure(self):
        row, calls = self.collect_runs([
            {"event": "schedule", "head_sha": "current", "conclusion": "success"},
            {"event": "push", "head_sha": "current", "conclusion": "failure"},
        ])
        self.assertEqual(row["ci"], "red")
        self.assertIn("repos/example-owner/example-game/actions/runs?branch=main&event=push&per_page=20", calls)

    def test_push_workflows_on_latest_commit_are_aggregated(self):
        row, _ = self.collect_runs([
            {"event": "push", "head_sha": "current", "conclusion": "success"},
            {"event": "push", "head_sha": "current", "status": "in_progress"},
        ])
        self.assertEqual(row["ci"], "running")

    def test_schedule_only_is_absent(self):
        row, _ = self.collect_runs([
            {"event": "schedule", "head_sha": "current", "conclusion": "success"},
        ])
        self.assertEqual(row["ci"], "absent")

    def test_older_push_failure_does_not_affect_latest_commit(self):
        row, _ = self.collect_runs([
            {"event": "push", "head_sha": "current", "conclusion": "success"},
            {"event": "push", "head_sha": "previous", "conclusion": "failure"},
        ])
        self.assertEqual(row["ci"], "green")

    def test_push_failure_and_running_states_take_priority(self):
        for state in ("failure", "timed_out", "startup_failure", "in_progress", "queued", "waiting", "pending", "requested"):
            with self.subTest(state=state):
                failed = state in ("failure", "timed_out", "startup_failure")
                row, _ = self.collect_runs([
                    {"event": "push", "head_sha": "current", "conclusion": "success"},
                    {"event": "push", "head_sha": "current", "status": "queued"},
                    {"event": "push", "head_sha": "current", "conclusion" if failed else "status": state},
                ])
                self.assertEqual(row["ci"], "red" if failed else "running")

    def test_skipped_and_neutral_push_runs_do_not_set_ci_health(self):
        ignored = [
            {"event": "push", "head_sha": "current", "conclusion": "skipped"},
            {"event": "push", "head_sha": "current", "conclusion": "neutral"},
        ]
        row, _ = self.collect_runs(ignored)
        self.assertEqual(row["ci"], "absent")
        row, _ = self.collect_runs(ignored + [
            {"event": "push", "head_sha": "current", "conclusion": "success"},
        ])
        self.assertEqual(row["ci"], "green")


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.health = health.collect(PROJECTS, health.Gh(runner(world())), NOW, "local")
        self.rows = {row["name"]: row for row in self.health["repos"]}

    def test_counts(self):
        self.assertEqual((self.health["ok"], self.health["total"], self.health["checked"]),
                         (2, 3, "2026-11-19T20:00:00+00:00"))
        self.assertEqual(self.health["by"], "local")
        self.assertLessEqual(self.health["requests"], 20)

    def test_repository_ci_pr_and_dispatch_health(self):
        row = self.rows["example-game"]
        self.assertEqual(row["ci"], "red")
        self.assertEqual(row["prs"], {"total": 2})
        self.assertEqual(row["waiting_merge"], [{"pr": 40, "minutes": 120}])
        self.assertEqual((row["needs_owner"], row["needs_author"], row["needs_arch"]), ([55], [40], [41]))
        self.assertEqual(row["dispatcher"]["result"], "done", "Ordinary discussion must not replace a dispatch record")
        self.assertEqual(row["last_merge"]["pr"], 39)
        self.assertEqual(row["light"], "red")
        for text in ("Default branch CI is red", "Dispatcher: done",
                     "1 PRs waiting for merge for more than an hour", "1 items need the owner",
                     "1 items need the author", "1 items need architecture review"):
            self.assertIn(text, row["line"])
        self.assertNotIn("autopilot", row)

    def test_one_repository_failure_keeps_other_fields_and_repositories(self):
        row = self.rows["demo-app"]
        self.assertEqual(row["error"], "GitHub read failed")
        self.assertNotIn("Private upstream", json.dumps(self.health))
        self.assertEqual((row["light"], row["ci"]), ("red", "green"))
        quiet = self.rows["demo-app (healthy)"]
        self.assertEqual((quiet["light"], quiet["line"], quiet["ci"]), ("green", "No action needed", "absent"))

    def test_lights(self):
        base = {"error": None, "ci": "green", "dispatcher": None, "waiting_merge": [],
                "needs_owner": [], "needs_author": [], "needs_arch": []}
        self.assertEqual(health.light_and_line(base, NOW), ("green", "No action needed"))
        for result, light in (("paused", "yellow"), ("publication_unknown", "yellow"), ("failed", "red")):
            with self.subTest(result=result):
                self.assertEqual(health.light_and_line({**base, "dispatcher": {"result": result}}, NOW)[0], light)
        for minutes, light in ((30, "green"), (60, "green"), (61, "yellow")):
            with self.subTest(minutes=minutes):
                row = {**base, "waiting_merge": [{"pr": 1, "minutes": minutes}]}
                self.assertEqual(health.light_and_line(row, NOW)[0], light)

    def test_same_input_same_output(self):
        again = health.collect(PROJECTS, health.Gh(runner(world())), NOW, "local")
        self.assertEqual(json.dumps(self.health), json.dumps(again))

    def test_uses_repository_default_branch_and_latest_dispatch_id(self):
        responses, calls = world(), []
        game = "repos/example-owner/example-game"
        responses[game] = {"default_branch": "develop"}
        responses[f"{game}/actions/runs?branch=develop&event=push&per_page=20"] = responses.pop(
            f"{game}/actions/runs?branch=main&event=push&per_page=20")
        responses[f"{game}/issues/12/comments?per_page=100&page=1"].append(
            comment("SoloForge result: paused.", "2026-11-19T19:30:00Z", 9))
        result = health.collect([PROJECTS[0]], health.Gh(runner(responses, calls)), NOW)
        self.assertIn(f"{game}/actions/runs?branch=develop&event=push&per_page=20", calls)
        self.assertEqual(result["repos"][0]["dispatcher"]["result"], "paused")

    def test_reads_latest_page_of_dispatch_comments(self):
        responses, calls = world(), []
        game = "repos/example-owner/example-game"
        responses[f"{game}/issues?state=open&per_page=100"][0]["comments"] = 201
        responses[f"{game}/issues/12/comments?per_page=100&page=3"] = responses.pop(
            f"{game}/issues/12/comments?per_page=100&page=1")
        health.collect([PROJECTS[0]], health.Gh(runner(responses, calls)), NOW)
        self.assertIn(f"{game}/issues/12/comments?per_page=100&page=3", calls)
        self.assertNotIn(f"{game}/issues/12/comments?per_page=100&page=1", calls)

    def test_owner_author_or_architecture_pending_is_yellow(self):
        for field in ("needs_owner", "needs_author", "needs_arch"):
            with self.subTest(field=field):
                row = {"ci": "green", field: [1]}
                self.assertEqual(health.light_and_line(row, NOW)[0], "yellow")

    def test_invalid_project_inputs_are_rejected_before_requests(self):
        with self.assertRaisesRegex(ValueError, "must be a list"):
            health.collect({"repo": "example-owner/example-game"})
        calls = []
        with self.assertRaisesRegex(ValueError, "owner/repository"):
            health.collect([{"repo": "example-game"}], health.Gh(runner({}, calls)), NOW)
        self.assertEqual(calls, [])

    def test_future_timestamp_does_not_produce_negative_wait(self):
        self.assertEqual(health.minutes_since(NOW, "2026-11-20T20:00:00Z"), 0)


def fake_gh(argv):
    """Answer `gh api <path>` from offline fixture data."""
    response = world().get(argv[-1])
    if response is None or response == 403:
        print("gh: HTTP 404" if response is None else "gh: HTTP 403", file=sys.stderr)
        return 1
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "fake-gh":
        sys.exit(fake_gh(sys.argv[2:]))
    unittest.main()
