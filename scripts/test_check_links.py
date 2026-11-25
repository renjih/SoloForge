"""Exercise local Markdown link validation in an isolated repository."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CheckLinksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="check-links-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True)
        self.checker = self.repo / "scripts/check-links.py"
        self.checker.parent.mkdir()
        shutil.copyfile(ROOT / "scripts/check-links.py", self.checker)

    def write(self, name, content=""):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def check(self):
        return subprocess.run(
            [sys.executable, str(self.checker)], cwd=self.repo,
            capture_output=True, text=True,
        )

    def test_valid_relative_root_encoded_and_reference_targets(self):
        self.write("LICENSE", "Example license")
        self.write("docs/guide.md", "# Guide")
        self.write("docs/a file.txt", "Example file")
        self.write("docs/nested/readme.md", "\n".join([
            "[Guide](../guide.md#guide)",
            "[License](/LICENSE)",
            "[Encoded](../a%20file.txt)",
            "[Directory](../)",
            "[Guide reference][guide]",
            "[guide]: ../guide.md \"Project guide\"",
        ]))
        subprocess.run(["git", "add", "LICENSE"], cwd=self.repo, check=True)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("5 local links checked", result.stdout)
        self.assertIn("(0 broken)", result.stdout)

    def test_missing_inline_and_reference_targets_fail(self):
        self.write("README.md", "\n".join([
            "# Example",
            "[Missing](missing.md)",
            "[missing]: docs/not-present.md",
        ]))
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("README.md:2: Broken local link: missing.md", result.stdout)
        self.assertIn("README.md:3: Broken local link: docs/not-present.md", result.stdout)
        self.assertIn("(2 broken)", result.stdout)

    def test_existing_target_outside_repository_fails(self):
        (self.directory / "outside.md").write_text("Outside", encoding="utf-8")
        self.write("README.md", "[Outside](../outside.md)")
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Broken local link: ../outside.md", result.stdout)

    def test_examples_external_urls_and_heading_links_are_ignored(self):
        self.write("README.md", "\n".join([
            "`[Inline example](missing.md)`",
            "```markdown",
            "[Fenced example](missing.md)",
            "```",
            "~~~markdown",
            "[Other fence](missing.md)",
            "~~~",
            "[External](https://example.com/missing.md)",
            "[Heading](#example)",
        ]))
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 local links checked", result.stdout)


if __name__ == "__main__":
    unittest.main()
