"""Validate the dispatcher composite boundary and its public caller template."""

import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class DispatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.action = load(ROOT / "actions/dispatch/action.yml")
        self.template = load(ROOT / "templates/dispatch.yml")

    def test_composite_is_a_thin_python_shell_with_input_environment(self):
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        step = self.action["runs"]["steps"][0]
        self.assertEqual(step["id"], "dispatch")
        self.assertEqual(step["shell"], "bash")
        self.assertEqual(step["run"].strip(), 'python3 "$GITHUB_ACTION_PATH/dispatch.py"')
        inputs = {
            "SF_GITHUB_TOKEN": "github-token",
            "SF_REPOSITORY": "repository",
            "SF_WORKDIR": "workdir",
            "SF_TRIGGERS": "triggers",
            "SF_ALLOWED_ASSOCIATIONS": "allowed-associations",
            "SF_PRE_TASK": "pre_task",
            "SF_POST_TASK": "post_task",
            "SF_AUTH_SECRET_TOKEN": "auth-secret-token",
            "SF_TIMEOUT_MINUTES": "timeout-minutes",
            "SF_MAX_TURNS": "max-turns",
            "SF_MAX_TOKENS": "max-tokens",
        }
        self.assertEqual(set(step["env"]), set(inputs))
        for environment, parameter in inputs.items():
            self.assertEqual(step["env"][environment], "${{ inputs." + parameter + " }}")

    def test_composite_defaults_and_outputs_match_dispatch_contract(self):
        inputs = self.action["inputs"]
        self.assertEqual(inputs["github-token"]["required"], "true")
        self.assertEqual(inputs["repository"]["default"], "${{ github.repository }}")
        self.assertEqual(inputs["workdir"]["default"], "${{ runner.temp }}/soloforge-work")
        self.assertEqual(json.loads(inputs["triggers"]["default"]), {"/codex": "codex", "/claude": "claude"})
        self.assertEqual(json.loads(inputs["allowed-associations"]["default"]), ["OWNER"])
        for name in ("pre_task", "post_task", "auth-secret-token"):
            self.assertEqual(inputs[name]["default"], "")
        for name, default in (("timeout-minutes", "30"), ("max-turns", "21"), ("max-tokens", "244000")):
            self.assertEqual(inputs[name]["default"], default)
        self.assertEqual(set(self.action["outputs"]), {"result", "branch", "pr_url"})
        for name, output in self.action["outputs"].items():
            self.assertEqual(output["value"], "${{ steps.dispatch.outputs." + name + " }}")

    def test_composite_has_no_secrets_context_or_concurrency(self):
        text = (ROOT / "actions/dispatch/action.yml").read_text(encoding="utf-8")
        self.assertNotIn("secrets.", text)
        self.assertNotIn("concurrency", self.action)
        self.assertNotIn("concurrency", self.action["runs"])

    def test_template_runs_owner_comments_on_hosted_runner(self):
        self.assertEqual(self.template["on"], {"issue_comment": {"types": ["created"]}})
        self.assertEqual(self.template["permissions"], {
            "contents": "write", "issues": "write", "pull-requests": "write",
        })
        self.assertEqual(set(self.template["jobs"]), {"dispatch"})
        job = self.template["jobs"]["dispatch"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["timeout-minutes"], "120")
        self.assertIn("github.event.comment.author_association == 'OWNER'", job["if"])
        self.assertNotIn("vars.CI_RUNNER", str(self.template))
        self.assertNotIn("pull_request_target", self.template["on"])

    def test_template_serializes_one_issue_without_cancelling_work(self):
        concurrency = self.template["concurrency"]
        self.assertEqual(concurrency["cancel-in-progress"], "false")
        self.assertIn("${{ github.repository }}", concurrency["group"])
        self.assertIn("${{ github.event.issue.number }}", concurrency["group"])

    def test_template_passes_credentials_only_to_dispatch_step(self):
        steps = self.template["jobs"]["dispatch"]["steps"]
        dispatch = steps[-1]
        self.assertTrue(dispatch["uses"].endswith("/actions/dispatch@v0"))
        self.assertEqual(dispatch["with"]["github-token"], "${{ secrets.SOLOFORGE_TOKEN || github.token }}")
        self.assertEqual(dispatch["with"]["auth-secret-token"], "${{ secrets.SOLOFORGE_AUTH_TOKEN }}")
        self.assertEqual(dispatch["with"]["timeout-minutes"], "30")
        self.assertEqual(dispatch["with"]["max-turns"], "21")
        self.assertEqual(dispatch["with"]["max-tokens"], "244000")
        self.assertEqual(dispatch["env"], {
            "CODEX_AUTH_JSON": "${{ secrets.CODEX_AUTH_JSON }}",
            "OPENAI_API_KEY": "${{ secrets.OPENAI_API_KEY }}",
            "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
        })
        for step in steps[:-1]:
            self.assertNotIn("secrets.", str(step))
        self.assertTrue(any(step.get("uses", "").startswith("actions/setup-node@") for step in steps))
        self.assertTrue(any(step.get("uses", "").startswith("actions/setup-python@") for step in steps))

    def test_executable_blocks_never_interpolate_event_fields(self):
        for step in self.action["runs"]["steps"] + self.template["jobs"]["dispatch"]["steps"]:
            with self.subTest(step=step.get("name") or step.get("uses")):
                self.assertNotIn("${{", step.get("run", ""))
                self.assertNotIn("${{", step.get("with", {}).get("script", ""))


if __name__ == "__main__":
    unittest.main()
