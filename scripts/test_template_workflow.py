"""Validate the runnable mini-game starter and its shared workflow contracts."""

import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/wechat-minigame"


def load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class TemplateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.ci = load(TEMPLATE / ".github/workflows/ci.yml")
        self.dispatch = load(TEMPLATE / ".github/workflows/dispatch.yml")

    def test_gate_and_optional_merge_use_the_same_workflow_repository(self):
        dispatch_step = self.dispatch["jobs"]["dispatch"]["steps"][-1]
        namespace = dispatch_step["uses"].split("/actions/dispatch@", 1)[0]
        gate = self.ci["jobs"]["gate"]
        merge = self.ci["jobs"]["merge"]
        self.assertTrue(namespace.endswith("/SoloForge"))
        self.assertEqual(gate["uses"], namespace + "/.github/workflows/gate.yml@main")
        self.assertEqual(merge["uses"], namespace + "/.github/workflows/merge-bot.yml@main")
        self.assertEqual(gate["permissions"], {"contents": "read", "pull-requests": "write"})
        self.assertEqual(gate["with"], {"stack": "node", "check": "npm run check"})
        self.assertEqual(merge["permissions"], {})
        self.assertIn("vars.SOLOFORGE_AUTO_MERGE == 'true'", merge["if"])
        self.assertEqual(merge["secrets"]["token"], "${{ secrets.SOLOFORGE_TOKEN }}")

    def test_forks_are_excluded_from_merges_and_label_events_run_gate(self):
        self.assertNotIn("pull_request_target", self.ci["on"])
        self.assertEqual(self.ci["jobs"]["gate"]["if"],
                         "github.event_name == 'pull_request' || github.event_name == 'push'")
        self.assertIn("labeled", self.ci["on"]["pull_request"]["types"])
        self.assertIn("unlabeled", self.ci["on"]["pull_request"]["types"])
        self.assertIn("github.event_name != 'pull_request' ||", self.ci["jobs"]["merge"]["if"])
        self.assertIn("github.event.pull_request.head.repo.full_name == github.event.pull_request.base.repo.full_name",
                      self.ci["jobs"]["merge"]["if"])

    def test_dispatch_stays_in_sync_with_the_board_and_cost_template(self):
        self.assertEqual(self.dispatch, load(ROOT / "templates/dispatch.yml"))
        job = self.dispatch["jobs"]["dispatch"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertIn("github.event.comment.author_association == 'OWNER'", job["if"])
        inputs = job["steps"][-1]["with"]
        self.assertEqual(inputs["timeout-minutes"], "30")
        self.assertEqual(inputs["max-turns"], "21")
        self.assertEqual(inputs["max-tokens"], "244000")
        self.assertIn("dispatch_hook.py", inputs["pre_task"])
        self.assertIn("dispatch_hook.py", inputs["post_task"])
        self.assertEqual(self.dispatch["concurrency"]["cancel-in-progress"], "false")

    def test_executable_blocks_do_not_interpolate_event_data(self):
        for workflow in (self.ci, self.dispatch):
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    with self.subTest(step=step.get("name") or step.get("uses")):
                        self.assertNotIn("${{", step.get("run", ""))
                        self.assertNotIn("${{", step.get("with", {}).get("script", ""))

    def test_fictional_metadata_and_twelve_retained_regressions(self):
        for name in ("package.json", "package-lock.json"):
            document = json.loads((TEMPLATE / name).read_text(encoding="utf-8"))
            self.assertEqual(document["name"], "example-game")
        project = json.loads((TEMPLATE / "project.config.json").read_text(encoding="utf-8"))
        self.assertEqual(project["projectname"], "example-game")
        self.assertEqual(project["appid"], "touristappid")
        cases = sum(len(re.findall(r"^test\(", path.read_text(encoding="utf-8"), re.M))
                    for path in (TEMPLATE / "test").glob("*.test.mjs"))
        self.assertEqual(cases, 12)


if __name__ == "__main__":
    unittest.main()
