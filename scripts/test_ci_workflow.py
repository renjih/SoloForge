"""Exercise workflow configuration and the repository privacy scan."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
HEAD_REPO = "github.event.pull_request.head.repo.full_name"
BASE_REPO = "github.event.pull_request.base.repo.full_name"
SAME_REPO = f"github.event_name != 'pull_request' || {HEAD_REPO} == {BASE_REPO}"


def load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def oneline(text):
    return " ".join(text.split())


class CiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.ci = load(WORKFLOWS / "ci.yml")
        self.template = load(ROOT / "templates/ci.yml")

    def test_own_ci_calls_local_workflows(self):
        jobs = self.ci["jobs"]
        self.assertEqual(jobs["gate"]["uses"], "./.github/workflows/gate.yml")
        self.assertEqual(jobs["merge"]["uses"], "./.github/workflows/merge-bot.yml")
        self.assertIn("bash scripts/check.sh", jobs["gate"]["with"]["check"])

    def test_template_uses_repository_placeholders(self):
        jobs = self.template["jobs"]
        for job, workflow in (("gate", "gate"), ("merge", "merge-bot")):
            self.assertEqual(jobs[job]["uses"], f"YOUR_ORG/YOUR_WORKFLOW_REPO/.github/workflows/{workflow}.yml@main")

    def test_gate_skipping_events_queue_apart_from_code_events(self):
        gate_condition = self.ci["jobs"]["gate"]["if"]
        self.assertIn("github.event_name == 'push'", gate_condition)
        code_events = re.search(r"fromJSON\('(\[[^']*\])'\)", gate_condition)
        self.assertIsNotNone(code_events, "Gate must select code events")
        code = set(json.loads(code_events[1]))
        self.assertEqual(code, {"opened", "synchronize", "reopened"})
        skips_gate = set(self.ci["on"]["pull_request"]["types"]) - code
        self.assertEqual(skips_gate, {"labeled", "unlabeled"})
        group = self.ci["concurrency"]["group"]
        queued_apart = re.search(r"contains\(fromJSON\('(\[[^']*\])'\), github\.event\.action\)", group)
        self.assertIsNotNone(queued_apart, "Label events need a separate concurrency group")
        self.assertEqual(set(json.loads(queued_apart[1])), skips_gate)

    def test_template_gate_runs_on_all_pull_request_events(self):
        self.assertEqual(
            self.template["jobs"]["gate"]["if"],
            "github.event_name == 'pull_request' || github.event_name == 'push'",
        )
        self.assertIn("labeled", self.template["on"]["pull_request"]["types"])
        self.assertIn("unlabeled", self.template["on"]["pull_request"]["types"])

    def test_merge_runs_on_same_repo_label_events_and_schedule(self):
        for workflow in (self.ci, self.template):
            with self.subTest(gate=workflow["jobs"]["gate"]["uses"]):
                condition = oneline(workflow["jobs"]["merge"]["if"])
                self.assertEqual(condition, f"always() && github.event_name != 'push' && ({SAME_REPO})")
                self.assertEqual(workflow["jobs"]["merge"]["needs"], ["gate"])
                self.assertIn("schedule", workflow["on"])
                self.assertIn("labeled", workflow["on"]["pull_request"]["types"])
                self.assertIn("unlabeled", workflow["on"]["pull_request"]["types"])
                self.assertNotIn("pull_request_target", workflow["on"])

    def test_callers_grant_the_gate_permissions(self):
        gate = load(WORKFLOWS / "gate.yml")["jobs"]["gate"]
        self.assertEqual(gate["permissions"], {"contents": "read", "pull-requests": "write"})
        for workflow in (self.ci, self.template):
            self.assertEqual(workflow["jobs"]["gate"]["permissions"], gate["permissions"])


class ReusableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.gate = load(WORKFLOWS / "gate.yml")["jobs"]["gate"]
        self.merge = load(WORKFLOWS / "merge-bot.yml")["jobs"]["merge"]

    def test_fork_runner_overrides_self_hosted_runner(self):
        self.assertEqual(
            oneline(self.gate["runs-on"]),
            "$" + "{{ " + f"(github.event_name == 'pull_request' && {HEAD_REPO} != {BASE_REPO}) "
            "&& 'ubuntu-latest' || (vars.CI_RUNNER || 'ubuntu-latest') }}",
        )
        self.assertEqual(self.gate["steps"][0]["with"]["persist-credentials"], "false")

    def test_merge_bot_only_uses_slim_runner_for_eligible_events(self):
        self.assertEqual(oneline(self.merge["if"]), SAME_REPO)
        self.assertEqual(self.merge["runs-on"], "${{ vars.CI_RUNNER || 'ubuntu-slim' }}")
        self.assertLessEqual(int(self.merge["timeout-minutes"]), 15)

    def test_clean_scan_uses_its_workflow_revision_before_project_code(self):
        steps = self.gate["steps"]
        index = next(i for i, step in enumerate(steps) if step.get("name") == "Check for private data")
        self.assertIn("bash scripts/check-clean.sh", steps[index]["run"])
        self.assertEqual(steps[index]["env"]["WORKFLOW_REPOSITORY"], "${{ job.workflow_repository }}")
        self.assertEqual(steps[index]["env"]["WORKFLOW_SHA"], "${{ job.workflow_sha }}")
        self.assertTrue(all("run" not in step for step in steps[:index]))

    def test_shell_and_script_blocks_use_runtime_values(self):
        for name, job in (("gate", self.gate), ("merge-bot", self.merge)):
            for step in job["steps"]:
                with self.subTest(workflow=name, step=step.get("name") or step.get("uses")):
                    self.assertNotIn("$" + "{{", step.get("run", ""))
                    self.assertNotIn("$" + "{{", step.get("with", {}).get("script", ""))
        self.assertEqual(self.merge["steps"][0]["env"]["REPO_OWNER"], "${{ github.repository_owner }}")


class CheckCleanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="check-clean-")
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True)
        self.scanner = self.repo / "scripts/check-clean.sh"
        self.scanner.parent.mkdir()
        shutil.copyfile(ROOT / "scripts/check-clean.sh", self.scanner)

    def write(self, name, content):
        (self.repo / name).write_text(content, encoding="utf-8")

    def scan(self):
        return subprocess.run(["bash", str(self.scanner)], cwd=self.repo, capture_output=True, text=True)

    def test_ignores_its_own_patterns_and_allows_examples_and_runner_paths(self):
        self.write("examples.txt", "\n".join([
            "sample@example.com allowed@example.invalid",
            "noreply@demo-app.test 123+sample@users.noreply.github.com",
            "'noreply@demo-app.test'",
            "task-status",
            "#" + "12345",
            "/" + "users/alice/file",
            "C:" + r"\users\alice\file",
            "/" + "Home/alice/file",
            "/" + "home/runner/work",
            "/" + "home/runneradmin/work",
            "/" + "home/ghrunner/work",
        ]))
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "check-clean: 0 matches")

    def test_flags_generic_patterns_without_echoing_the_values(self):
        cases = [
            ("private key header", "BEGIN " + "RSA PRIVATE" + " KEY"),
            ("token prefix", "gh" + "p_" + "example"),
            ("token prefix", "github_" + "pat_" + "example"),
            ("token prefix", "s" + "k-example"),
            ("token prefix", "AK" + "IAEXAMPLE"),
            ("spreadsheet link", "https://docs.google.com/spread" + "sheets/d/" + "example"),
            ("local path", "/" + "Users/alice/file"),
            ("local path", "C:" + r"\Users\alice\file"),
            ("local path", "C:" + r"\\Users\\alice\\file"),
            ("local home path", "/" + "home/alice/file"),
            ("email address", "sample" + "@demo-app.test"),
        ]
        for index, (_, value) in enumerate(cases):
            self.write(f"example {index}.txt", "Example on next line\n" + value)
        subprocess.run(["git", "add", "example 0.txt"], cwd=self.repo, check=True)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for index, (kind, value) in enumerate(cases):
            with self.subTest(kind=kind, index=index):
                self.assertIn(f"'example {index}.txt':2: {kind}", result.stdout)
                self.assertNotIn(value, result.stdout)
        self.assertIn(f"check-clean: {len(cases)} matches", result.stdout)

    def test_allowed_email_does_not_mask_another_address(self):
        self.write("addresses.txt", "sample@example.com rejected" + "@demo-app.test")
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("'addresses.txt':1: email address", result.stdout)


if __name__ == "__main__":
    unittest.main()
