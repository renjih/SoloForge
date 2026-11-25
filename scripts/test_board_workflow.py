"""Validate the board maintenance workflow's trusted execution boundary."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class BoardWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github/workflows/board.yml").read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.text, Loader=yaml.BaseLoader)

    def test_only_scheduled_and_manual_events_can_run_maintenance(self):
        self.assertEqual(set(self.workflow["on"]), {"schedule", "workflow_dispatch"})
        self.assertEqual(self.workflow["on"]["schedule"], [{"cron": "41 */2 * * *"}])

    def test_maintenance_uses_hosted_runner_and_scoped_permissions(self):
        job = self.workflow["jobs"]["maintain"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["permissions"], {
            "contents": "write", "pull-requests": "read", "issues": "read", "actions": "read",
        })
        self.assertEqual(job["timeout-minutes"], "15")
        self.assertNotIn("vars.CI_RUNNER", self.text)

    def test_owner_repository_and_token_arrive_through_environment(self):
        env = self.workflow["jobs"]["maintain"]["env"]
        self.assertEqual(env["SOLOFORGE_OWNER"], "${{ github.repository_owner }}")
        self.assertEqual(env["SF_REPOSITORY"], "${{ github.repository }}")
        self.assertEqual(env["GH_TOKEN"], "${{ github.token }}")
        self.assertNotIn("x-access-token:", self.text)
        self.assertNotIn("https://", self.text)

    def test_tools_are_checked_out_without_persisted_credentials(self):
        steps = self.workflow["jobs"]["maintain"]["steps"]
        self.assertEqual(steps[0]["uses"], "actions/checkout@v4")
        self.assertEqual(steps[0]["with"]["persist-credentials"], "false")
        maintenance = [step for step in steps if "run" in step]
        self.assertEqual(len(maintenance), 1)
        self.assertEqual(maintenance[0]["run"], "python3 tools/board/maintain.py")
        for step in steps:
            self.assertNotIn("${{", step.get("run", ""))

    def test_writers_share_repository_queue_without_cancellation(self):
        concurrency = self.workflow["concurrency"]
        self.assertEqual(concurrency["group"], "soloforge-board-${{ github.repository }}")
        self.assertEqual(concurrency["cancel-in-progress"], "false")


if __name__ == "__main__":
    unittest.main()
