"""Retained patrol contracts and caller-owned configuration tests; no network."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import quota_patrol as qp

NOW = datetime(2026, 11, 10, 23, 41, tzinfo=timezone.utc)


def body(level="normal", remaining=52, read="2026-11-10 07:50:41 UTC", pushed=None, actions="normal"):
    pushed = pushed or "level: normal\nPushed tasks: none\nPushed at: 2026-11-09 UTC"
    return (f"Ledger\n\n{qp.STATUS_START}\nlevel: {level}\n"
            f"Weekly quota: remaining {remaining}%; resets 2026-11-15 21:10:54 UTC\n"
            f"Read at: {read}\nActions this month: 213.4 minutes (7.1%), {actions}; estimated\n"
            f"{qp.STATUS_END}\n\n{qp.PUSHED_START}\n{pushed}\n{qp.PUSHED_END}\n")


def record(model="writer-model", effort="high", before=10, after=13,
           time="2026-11-10T12:00:00+00:00", reset_b=1, reset_a=1, **extra):
    data = {"repo": "example-owner/example-game", "branch": "codex/task", "mode": "issue", "tier": "heavy",
            "model": model, "effort": effort, "time": time, "duration_sec": 60,
            "quota_before": {"weekly": {"used_percent": before, "resets_at": reset_b}},
            "quota_after": {"weekly": {"used_percent": after, "resets_at": reset_a}},
            "tokens": {"input": 1000, "cached_input": 800, "output": 50}, **extra}
    return {"body": "```json\n" + json.dumps(data) + "\n```", "created_at": time}


def trig(name="example-game", status="ROUTINE_RUN_STATUS_SUCCEEDED", fired="2026-11-10T20:00:00Z",
         finished="2026-11-10T20:10:00Z", tid="trigger-a"):
    return {"id": tid, "name": name, "derived_state": {"model": "reviewer-model"},
            "last_run": {"status": status, "fired_at": fired, "finished_at": finished}}


class ParseTest(unittest.TestCase):
    def test_status(self):
        status = qp.parse_status(body())
        self.assertEqual(status["level"], "normal")
        self.assertEqual(status["weekly_remaining"], 52)
        self.assertEqual(status["weekly_reset"].hour, 21)
        self.assertFalse(status["actions_low"])

    def test_old_pushed_format(self):
        self.assertEqual(qp.parse_pushed(body()), {"level": "normal", "tasks": [], "unknown_at": None, "actions": None})

    def test_pushed_roundtrip(self):
        text = qp.render_pushed("low", ["trigger-a@2026-11-10T20:00:00+00:00"], NOW, NOW)
        pushed = qp.parse_pushed(body(pushed=text))
        self.assertEqual(pushed["level"], "low")
        self.assertEqual(pushed["tasks"], ["trigger-a@2026-11-10T20:00:00+00:00"])
        self.assertEqual(pushed["unknown_at"], NOW)

    def test_replace_pushed_keeps_status(self):
        original = body()
        updated = qp.replace_pushed(original, "level: low")
        self.assertIn("level: low", updated)
        self.assertEqual(qp.section(updated, qp.STATUS_START, qp.STATUS_END), qp.section(original, qp.STATUS_START, qp.STATUS_END))

    def test_quota_delta(self):
        records, _ = qp.parse_ledger([record(before=10, after=13), record(before=90, after=2),
                                     record(reset_b=1, reset_a=2), {"body": "ordinary discussion"}])
        self.assertEqual([qp.quota_delta(item) for item in records], [3, None, None])


class ReportTest(unittest.TestCase):
    def run_report(self, text=None, comments=(), triggers=()):
        return qp.report(text if text is not None else body(), list(comments), list(triggers), NOW)

    def test_quiet_normal(self):
        output = self.run_report(comments=[record()], triggers=[trig()])
        self.assertIsNone(output["push_message"])
        self.assertFalse(output["pushed_changed"])
        self.assertIn("writer-model high | today 1 runs", output["stats"][0])
        self.assertIsNotNone(output["daily_message"])

    def test_no_records(self):
        self.assertEqual(self.run_report()["stats"][0], "Dispatcher: no ledger records yet")

    def test_low_level_pushes_once(self):
        output = self.run_report(text=body(level="low", remaining=25))
        self.assertIn("weekly remaining 25%", output["push_message"])
        again = self.run_report(text=body(level="low", remaining=25, pushed=output["pushed_text"]))
        self.assertIsNone(again["push_message"])

    def test_recovery_silent(self):
        output = self.run_report(text=body(pushed="level: low\nPushed tasks: none"))
        self.assertIsNone(output["push_message"])
        self.assertTrue(output["pushed_changed"])
        self.assertTrue(output["pushed_text"].startswith("level: normal"))

    def test_failed_task_pushes_once(self):
        triggers = [trig(status="ROUTINE_RUN_STATUS_FAILED"), trig(name="Quota patrol", status="FAILED", tid="self")]
        output = self.run_report(triggers=triggers)
        self.assertIn("example-game failed", output["push_message"])
        self.assertNotIn("Quota patrol failed", output["push_message"])
        again = self.run_report(text=body(pushed=output["pushed_text"]), triggers=triggers)
        self.assertIsNone(again["push_message"])

    def test_two_failures_hint_usage(self):
        triggers = [trig(status="FAILED"), trig(name="demo-app", status="FAILED", tid="trigger-b")]
        self.assertIn("check provider usage", self.run_report(triggers=triggers)["push_message"])

    def test_stale_read_pushes_weekly(self):
        output = self.run_report(text=body(read="2026-11-01 07:50:41 UTC"))
        self.assertIn("more than 7 days", output["push_message"])
        again = self.run_report(text=body(read="2026-11-01 07:50:41 UTC", pushed=output["pushed_text"]))
        self.assertIsNone(again["push_message"])

    def test_actions_low(self):
        self.assertIn("Actions", self.run_report(text=body(actions="actions_low"))["push_message"])

    def test_actions_low_pushes_once(self):
        output = self.run_report(text=body(actions="actions_low"))
        self.assertTrue(output["pushed_changed"])
        again = self.run_report(text=body(actions="actions_low", pushed=output["pushed_text"]))
        self.assertIsNone(again["push_message"])
        self.assertFalse(again["pushed_changed"])

    def test_actions_recovery_resets_silently(self):
        first = self.run_report(text=body(actions="actions_low"))
        recovered = self.run_report(text=body(actions="normal", pushed=first["pushed_text"]))
        self.assertIsNone(recovered["push_message"])
        self.assertTrue(recovered["pushed_changed"])
        later = self.run_report(text=body(actions="actions_low", pushed=recovered["pushed_text"]))
        self.assertIn("Actions", later["push_message"])

    def test_actions_flag_kept_when_status_line_missing(self):
        first = self.run_report(text=body(actions="actions_low"))
        no_line = body(pushed=first["pushed_text"]).replace("Actions this month: 213.4 minutes (7.1%), normal; estimated\n", "")
        self.assertFalse(self.run_report(text=no_line)["pushed_changed"])

    def test_actions_unknown_is_not_recovery(self):
        first = self.run_report(text=body(actions="actions_low"))
        unknown = body(pushed=first["pushed_text"]).replace("Actions this month: 213.4 minutes (7.1%), normal; estimated",
                                                           "Actions this month: unknown (read error)")
        self.assertFalse(self.run_report(text=unknown)["pushed_changed"])
        again = self.run_report(text=body(actions="actions_low", pushed=first["pushed_text"]))
        self.assertIsNone(again["push_message"])

    def test_all_alerts_delivered_when_four_fire(self):
        output = self.run_report(text=body(level="low", remaining=25, read="2026-11-01 07:50:41 UTC", actions="actions_low"),
                                 triggers=[trig(status="FAILED")])
        self.assertEqual(len(output["alerts"]), 4)
        for text in ("weekly remaining 25%", "Actions", "example-game failed", "more than 7 days"):
            self.assertIn(text, output["push_message"])

    def test_daily_only_late_evening(self):
        output = qp.report(body(), [], [], datetime(2026, 11, 10, 18, 41, tzinfo=timezone.utc))
        self.assertIsNone(output["daily_message"])


class PublicConfigurationTest(unittest.TestCase):
    def test_owner_and_ledger_are_required_without_personal_defaults(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                qp.arguments(["report", "--repo", "example-game", "--ledger-issue", "42"])
            with self.assertRaises(SystemExit):
                qp.arguments(["report", "--owner", "example-owner", "--repo", "example-game"])
        with patch.dict(os.environ, {"SOLOFORGE_OWNER": "example-owner", "SOLOFORGE_LEDGER_ISSUE": "42"}):
            args = qp.arguments(["report", "--repo", "example-game"])
        self.assertEqual((args.owner, args.ledger_issue), ("example-owner", 42))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            qp.arguments(["report", "--owner", "example-owner", "--repo", "example-game", "--ledger-issue", "0"])

    def test_appended_dispatch_ledger_supplies_status_and_reported_tokens(self):
        output = qp.report("Ledger", [record(level="low", tokens=1500, before=70, after=75)], [], NOW)
        self.assertEqual(output["codex"]["weekly_remaining"], 25)
        self.assertEqual(output["codex"]["level"], "low")
        self.assertIn("1500 reported tokens", output["stats"][0])
        self.assertEqual(qp.token_counts({"tokens": 1500}), (1500, None))
        self.assertEqual(qp.token_counts({"tokens": {"input": 1000, "cached_input": 800, "output": 50}}), (1050, 250))

    def test_report_can_run_offline_and_uses_configured_issue_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "issue.json").write_text(json.dumps({"body": body(level="low", remaining=25)}), encoding="utf-8")
            (root / "comments.json").write_text("[]", encoding="utf-8")
            with patch.object(qp, "gh") as remote, redirect_stdout(io.StringIO()):
                code = qp.main(["report", "--owner", "example-owner", "--repo", "example-game", "--ledger-issue", "42",
                                "--issue-file", str(root / "issue.json"), "--comments-file", str(root / "comments.json"),
                                "--now", NOW.isoformat(), "--out", str(root / "out.json"), "--pushed-out", str(root / "pushed.txt")])
            self.assertEqual(code, 0)
            remote.assert_not_called()
            output = json.loads((root / "out.json").read_text())
            self.assertIn("https://github.com/example-owner/example-game/issues/42", output["push_message"])

    def test_mark_pushed_only_updates_configured_caller_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            pushed = Path(directory) / "pushed.txt"
            pushed.write_text("level: low", encoding="utf-8")
            original = body()
            with patch.object(qp, "gh", side_effect=[{"body": original}, {}]) as remote, redirect_stdout(io.StringIO()):
                self.assertEqual(qp.main(["mark-pushed", "--owner", "example-owner", "--repo", "demo-app", "--ledger-issue", "73",
                                          "--pushed", str(pushed)]), 0)
            self.assertEqual(remote.call_args_list[0].args, ("repos/example-owner/demo-app/issues/73",))
            updated = remote.call_args_list[1].kwargs["body"]["body"]
            self.assertEqual(qp.section(updated, qp.STATUS_START, qp.STATUS_END), qp.section(original, qp.STATUS_START, qp.STATUS_END))

    def test_duplicate_reversed_or_overlapping_markers_are_rejected(self):
        for original in (qp.PUSHED_START * 2 + qp.PUSHED_END,
                         qp.PUSHED_END + qp.PUSHED_START,
                         qp.STATUS_START + qp.PUSHED_START + qp.STATUS_END + qp.PUSHED_END,
                         qp.STATUS_START + qp.STATUS_END + qp.STATUS_START + qp.PUSHED_START + qp.STATUS_END + qp.PUSHED_END,
                         qp.STATUS_START + qp.PUSHED_START + qp.PUSHED_END):
            with self.subTest(original=original), self.assertRaises(RuntimeError):
                qp.replace_pushed(original, "level: low")

    def test_invalid_usage_is_not_treated_as_a_real_measurement(self):
        for value in (True, -1, float("nan"), 101):
            self.assertIsNone(qp.weekly_used({"weekly": {"used_percent": value}}))
        for value in (True, -1, {"input": 1, "cached_input": 2}, {"input": False}):
            self.assertIsNone(qp.token_counts({"tokens": value}))

    def test_quota_delta_requires_real_matching_reset_boundaries(self):
        for reset in (None, True, "unknown", float("nan"), float("inf"), -1):
            with self.subTest(reset=reset):
                records, _ = qp.parse_ledger([record(reset_b=reset, reset_a=reset)])
                self.assertIsNone(qp.quota_delta(records[0]))

    def test_nonquota_adapter_cannot_replace_last_codex_snapshot(self):
        output = qp.report("Ledger", [record(adapter="codex", level="low", before=70, after=75),
                                      record(adapter="claude", level="unknown", time="2026-11-10T12:10:00Z",
                                             quota_before=None, quota_after=None)], [], NOW)
        self.assertEqual(output["codex"]["level"], "low")
        self.assertEqual(output["codex"]["weekly_remaining"], 25)
        self.assertNotIn("unknown", output["alerts"])

    def test_owner_and_repository_components_cannot_traverse_endpoint(self):
        for owner, repo in ((".", "example-game"), ("..", "example-game"), ("-owner", "example-game"),
                            ("owner_unsafe", "example-game"), ("example-owner", "."), ("example-owner", "..")):
            with self.subTest(owner=owner, repo=repo), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                qp.arguments(["report", "--owner", owner, "--repo", repo, "--ledger-issue", "42"])

    def test_legacy_cache_excluded_and_reported_totals_stay_distinct(self):
        output = qp.report(body(), [record()], [], NOW)
        self.assertIn("1050 reported tokens", output["stats"][0])
        self.assertIn("250 legacy cache-excluded tokens", output["stats"][0])


if __name__ == "__main__":
    unittest.main()
