"""Port the source board lifecycle checks using public fixtures and local Git remotes."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("board_under_test", HERE / "board.py")
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
OWNER = "example-maintainer"


def run(command, *, cwd=None, env=None, input=None, check=True):
    result = subprocess.run(command, cwd=cwd, env=env, input=input, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
    if check and result.returncode:
        raise AssertionError(f"Command failed ({result.returncode}): {result.stdout}\n{result.stderr}")
    return result


class BoardTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="board-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}
        self.env.update(GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"), GIT_AUTHOR_NAME="Example Maintainer",
                        GIT_AUTHOR_EMAIL="maintainer@example.invalid", GIT_COMMITTER_NAME="Example Maintainer",
                        GIT_COMMITTER_EMAIL="maintainer@example.invalid", SOLOFORGE_OWNER=OWNER,
                        BOARD_NOW="2026-11-19T18:00:00+00:00", BOARD_REPO="example-game")
        self.remote = self.root / "hub.git"
        self.g(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        hub = self.root / "hub"
        self.g(self.root, "clone", str(self.remote), str(hub))
        tools = hub / "tools/board"
        tools.mkdir(parents=True)
        for name in ("board.py", "health.py", "board-hook.sh", "settings.json", "install.py"):
            shutil.copyfile(HERE / name, tools / name)
        (hub / "rules").mkdir()
        shutil.copyfile(HERE.parents[1] / "rules/BOARD.md", hub / "rules/BOARD.md")
        self.g(hub, "add", "--all")
        self.g(hub, "commit", "-m", "Tools")
        self.g(hub, "push", "origin", "main")
        self.g(hub, "checkout", "--orphan", "board")
        self.g(hub, "rm", "-rf", ".")
        (hub / "cards").mkdir()
        (hub / "cards/.gitkeep").write_text("")
        self.g(hub, "add", "cards")
        self.g(hub, "commit", "-m", "Initialize orphan data")
        self.g(hub, "push", "origin", "board")
        self.game = self.root / "example-game"
        self.g(self.root, "init", "--initial-branch=main", str(self.game))
        (self.game / "a.txt").write_text("a\n")
        self.g(self.game, "add", ".")
        self.g(self.game, "commit", "-m", "Initial example")
        run([sys.executable, str(HERE / "install.py"), str(self.game), "--url", str(self.remote)], env=self.env)
        self.g(self.game, "add", ".")
        self.g(self.game, "commit", "-m", "Install hooks")
        self.env.update(SOLOFORGE_URL=str(self.remote), BOARD_CACHE=str(self.root / "cache"),
                        BOARD_DIR=str(self.root / "cache/board"), CLAUDE_PROJECT_DIR=str(self.game))
        self.tool = self.root / "cache/main/tools/board/board.py"

    def g(self, cwd, *arguments):
        return run(["git", *arguments], cwd=cwd, env=self.env).stdout.strip()

    def hook(self, mode, payload=None, extra=None):
        return run(["bash", ".claude/hooks/board-hook.sh", mode], cwd=self.game,
                   env={**self.env, **(extra or {})}, input=json.dumps(payload or {"session_id": "session-one"}))

    def b(self, *arguments, env=None, check=True, cwd=None):
        return run([sys.executable, str(self.tool), *arguments], cwd=cwd or self.game,
                   env={**self.env, **(env or {})}, check=check)

    def card(self, identifier):
        return json.loads(next(Path(self.env["BOARD_DIR"]).glob(f"cards/*/{identifier}.json")).read_text(encoding="utf-8"))

    def remote_cards(self):
        return [name for name in self.g(self.remote, "ls-tree", "-r", "--name-only", "board").splitlines() if name.startswith("cards/") and name.endswith(".json")]

    def test_session_flow_blocks_once_then_passes(self):
        context = json.loads(self.hook("start").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Repository example-game", context)
        self.assertEqual(self.hook("stop").stdout.strip(), "")
        (self.game / "a.txt").write_text("changed\n")
        self.assertEqual(json.loads(self.hook("stop").stdout)["decision"], "block")
        identifier = self.b("new", "--title", "Implement parser", "--status", "in_progress", "--next", "Review parser").stdout.strip()
        self.assertEqual(self.hook("stop").stdout.strip(), "")
        self.assertEqual(len(self.remote_cards()), 1)
        self.assertEqual(self.hook("stop").stdout.strip(), "")
        (self.game / "a.txt").write_text("another change\n")
        self.assertEqual(self.hook("stop").stdout.strip(), "")
        self.g(self.game, "add", "a.txt")
        self.g(self.game, "commit", "-m", "Parser update")
        self.hook("stop")
        self.assertIn("Parser update", self.card(identifier)["today"])
        self.assertEqual(self.card(identifier)["history"][-1]["note"], "Stop hook appended 1 new commits")
        self.b("set", identifier, "--status", "done", "--by", "bot", cwd=self.root)
        self.hook("stop")
        (self.game / "a.txt").write_text("new work\n")
        self.assertEqual(json.loads(self.hook("stop").stdout)["decision"], "block")
        self.hook("start", {"session_id": "session-two"})
        (self.game / "a.txt").write_text("new session work\n")
        self.assertEqual(json.loads(self.hook("stop", {"session_id": "session-two"}).stdout)["decision"], "block")

    def test_second_stop_auto_card(self):
        self.hook("start")
        (self.game / "a.txt").write_text("changed\n")
        self.hook("stop", {"session_id": "session-one", "stop_hook_active": True})
        self.assertIn("[Missing handoff]", self.b("list").stdout)
        self.assertEqual(len(self.remote_cards()), 1)

    def test_bad_card_blocks_until_fixed(self):
        self.hook("start")
        identifier = self.b("new", "--title", "Example task", "--next", "Review").stdout.strip()
        path = next(Path(self.env["BOARD_DIR"]).glob(f"cards/*/{identifier}.json"))
        card = self.card(identifier)
        card["status"] = "done"
        path.write_text(json.dumps(card), encoding="utf-8")
        result = json.loads(self.hook("stop").stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("History does not match", result["reason"])
        self.hook("stop", {"session_id": "session-one", "stop_hook_active": True})
        self.assertIn("0 invalid", self.b("validate").stdout)
        self.assertFalse(path.exists())
        fallback = next(Path(self.env["BOARD_DIR"]).glob("cards/*/*.json"))
        self.assertEqual(json.loads(fallback.read_text())["rejected_handoffs"][0]["id"], identifier)

    def test_state_machine_rules(self):
        self.hook("start")
        self.assertIn("Only bot", self.b("new", "--title", "Example", "--status", "done", check=False).stderr)
        identifier = self.b("new", "--title", "Example", "--status", "in_progress", "--next", "Review").stdout.strip()
        self.assertIn("--ruled-out", self.b("set", identifier, "--status", "blocked", "--blocker", "Test failed", check=False).stderr)
        self.b("set", identifier, "--status", "blocked", "--blocker", "Test failed", "--ruled-out", "Retry did not help")
        self.b("set", identifier, "--status", "in_progress")
        self.assertIsNone(self.card(identifier)["blocker"])
        self.assertEqual(self.card(identifier)["blocker_last"]["what"], "Test failed")
        self.assertIn("next step", self.b("new", "--title", "Missing next", check=False).stderr)
        self.assertIn("wait_for_owner", self.b("new", "--title", "Inspect", "--status", "needs_owner", "--kind", "inspect",
                                               "--question", "Inspect the result", "--tier", "default_after_48h", check=False).stderr)
        self.b("new", "--title", "Inspect", "--status", "needs_owner", "--kind", "inspect", "--question", "Inspect the result")
        self.b("set", identifier, "--status", "done", "--by", OWNER)
        self.assertIn("terminal", self.b("set", identifier, "--status", "todo", check=False).stderr)
        self.b("new", "--title", "Reopened", "--next", "Review", "--reopens", identifier)

    def test_ticket_expires_to_recommended(self):
        self.hook("start")
        identifier = self.b("new", "--title", "Choose mode", "--status", "needs_owner", "--question", "Choose a mode",
                            "--option", "A: Standard", "--option", "B: Advanced", "--recommended", "A").stdout.strip()
        self.assertIn("--answer", self.b("set", identifier, "--status", "todo", "--next", "Apply mode", check=False).stderr)
        later = {"BOARD_NOW": "2026-11-22T18:00:00+00:00"}
        self.assertIn("Resolved 1", self.b("expire", "--dry", env=later).stdout)
        self.assertEqual(self.card(identifier)["status"], "needs_owner")
        self.b("expire", env=later)
        card = self.card(identifier)
        self.assertEqual((card["status"], card["decision"]["answer"], card["decision"]["answered_by"]), ("todo", "A", "default_after_48h"))
        self.assertIn("0 invalid", self.b("validate").stdout)
        context = json.loads(self.hook("start").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Decided: A", context)

    def test_reports_and_handoff(self):
        self.hook("start")
        for index in range(7):
            self.b("new", "--title", f"Decision {index}", "--status", "needs_owner", "--question", f"Question {index}",
                   "--option", "A: First", "--option", "B: Second", "--recommended", "A")
        self.b("new", "--title", "Account", "--status", "needs_owner", "--question", "Select account",
               "--option", "A: First", "--option", "B: Second", "--recommended", "A", "--tier", "wait_for_owner")
        self.b("new", "--title", "Register", "--status", "needs_owner", "--kind", "act", "--question", "Register the account")
        self.b("new", "--title", "Blocked", "--status", "blocked", "--blocker", "Tests failed", "--ruled-out", "Retry", "--next", "Inspect logs")
        evening = self.b("report", "evening", "--max", "5").stdout
        self.assertIn("Select account", evening.split("[Decisions] 8\n", 1)[1].splitlines()[0])
        self.assertIn("Another 3 decisions", evening)
        self.assertIn("[Owner actions] 1", evening)
        self.assertIn("Batch owner actions on Sunday", evening)
        self.assertIn("[Problems] 1", evening)
        self.assertIn("[First steps today]", self.b("report", "morning").stdout)
        self.assertIn("# example-game handoff", self.b("handoff", "--repo", "example-game").stdout)

    def test_codex_start_finish(self):
        self.hook("start")
        self.assertIn("fenced `board`", self.b("codex-start", "--session", "task-one").stdout)
        (self.game / "b.txt").write_text("change\n")
        self.g(self.game, "add", "b.txt")
        self.g(self.game, "commit", "-m", "Add example")
        summary = self.root / "summary.md"
        summary.write_text("Added example\n\n```board\n" + json.dumps({"title": "Add example", "status": "done",
                           "today": "Added a file", "state": "Ready", "next": "Review"}) + "\n```\n")
        self.b("codex-finish", "--summary", str(summary), "--branch", "codex/demo-app", "--pr", "7")
        card = json.loads(next(Path(self.env["BOARD_DIR"]).glob("cards/*/*.json")).read_text())
        self.assertEqual((card["title"], card["status"], card["pr"], card["files"]), ("Add example", "in_progress", 7, ["b.txt"]))
        stripped = run([sys.executable, str(self.tool), "strip"], input=summary.read_text(), env=self.env).stdout
        self.assertNotIn("```board", stripped)
        self.assertIn("Added example", stripped)
        self.b("task-start", "--session", "task-two", "--agent", "claude")
        (self.game / "c.txt").write_text("change\n")
        self.g(self.game, "add", "c.txt")
        self.g(self.game, "commit", "-m", "Another example")
        summary.write_text("Added another example\n")
        self.assertIn("Fallback card", self.b("task-finish", "--summary", str(summary), "--branch", "codex/another-example", "--pr", "8", "--agent", "claude").stdout)
        self.assertEqual(len(self.remote_cards()), 2)

    def test_merged_and_archive(self):
        self.hook("start")
        identifier = self.b("new", "--title", "Example", "--status", "in_progress", "--next", "Review", "--pr", "7").stdout.strip()
        previous = Path.cwd()
        try:
            os.chdir(self.game)
            with patch.dict(os.environ, {**self.env, "BOARD_ACTOR": "bot"}):
                with patch.object(board, "gh_json", return_value={"merged": True, "state": "closed"}):
                    board.cmd_sync_merged(None)
        finally:
            os.chdir(previous)
        self.assertEqual(self.card(identifier)["status"], "done")
        self.assertIn("Archived 0", self.b("archive").stdout)
        self.assertIn("Archived 1", self.b("archive", env={"BOARD_NOW": "2027-01-01T00:00:00+00:00"}).stdout)
        self.assertEqual(list(Path(self.env["BOARD_DIR"]).glob("cards/*/*.json")), [])

    def test_concurrent_push(self):
        self.hook("start")
        second = self.root / "second-board"
        self.g(self.root, "clone", "--branch", "board", str(self.remote), str(second))
        self.b("new", "--title", "First", "--next", "Review", env={"BOARD_DIR": str(second)})
        self.b("new", "--title", "Second", "--next", "Review")
        first = subprocess.Popen([sys.executable, str(self.tool), "push", "-m", "First card"], env={**self.env, "BOARD_DIR": str(second)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        other = subprocess.Popen([sys.executable, str(self.tool), "push", "-m", "Second card"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        outputs = [first.communicate(timeout=60), other.communicate(timeout=60)]
        self.assertEqual((first.returncode, other.returncode), (0, 0), outputs)
        self.assertEqual(len(self.remote_cards()), 2)

    def test_hook_fails_open_without_board(self):
        missing = {"SOLOFORGE_URL": str(self.root / "missing.git"), "BOARD_CACHE": str(self.root / "missing-cache"), "BOARD_DIR": str(self.root / "missing-board")}
        context = json.loads(self.hook("start", extra=missing).stdout)
        self.assertIn("Board unavailable", context["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.hook("stop", extra=missing).stdout.strip(), "")

    def test_reconfigured_board_never_uses_another_cached_remote(self):
        self.hook("start")
        changed = {"SOLOFORGE_URL": str(self.root / "different-hub.git")}
        context = json.loads(self.hook("start", extra=changed).stdout)
        self.assertIn("Board unavailable", context["hookSpecificOutput"]["additionalContext"])
        (self.game / "a.txt").write_text("changed\n")
        self.assertEqual(self.hook("stop", extra=changed).stdout.strip(), "")
        self.assertEqual(self.remote_cards(), [])

    def test_install_is_idempotent_and_keeps_other_settings(self):
        path = self.game / ".claude/settings.json"
        current = json.loads(path.read_text())
        current["other_setting"] = True
        current["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo other"}]})
        path.write_text(json.dumps(current))
        (self.game / "CLAUDE.md").write_text("# Existing instructions\n")
        for _ in range(2):
            run([sys.executable, str(HERE / "install.py"), str(self.game), "--url", str(self.remote)], env=self.env)
        current = json.loads(path.read_text())
        self.assertTrue(current["other_setting"])
        self.assertEqual(len(current["hooks"]["Stop"]), 2)
        self.assertEqual(len(current["hooks"]["SessionStart"]), 1)
        self.assertEqual((self.game / "AGENTS.md").read_text().count("<!-- board:begin -->"), 1)
        self.assertEqual((self.game / "CLAUDE.md").read_text().count("Board handoff"), 1)

    def test_other_agents_cards_pulled_in_do_not_count(self):
        self.hook("start")
        self.b("new", "--title", "Other agent", "--next", "Review", "--by", "codex", cwd=self.root)
        (self.game / "a.txt").write_text("changed\n")
        self.assertEqual(json.loads(self.hook("stop").stdout)["decision"], "block")

    def test_inject_capped_without_rules(self):
        self.hook("start")
        for index in range(14):
            self.b("new", "--title", f"Queued {index}", "--next", "Review")
        active = self.b("new", "--title", "Active task", "--status", "in_progress", "--next", "Review").stdout.strip()
        context = json.loads(self.hook("start").stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(sum(line.startswith("- ") for line in context.splitlines()), 12)
        self.assertIn(active, context)
        self.assertIn("Another 3 cards", context)
        self.assertNotIn(TASK_RULES_TITLE, context)

    def test_author_bulk_done(self):
        self.hook("start")
        first = self.b("new", "--title", "First", "--next", "Review").stdout.strip()
        second = self.b("new", "--title", "Second", "--next", "Review").stdout.strip()
        attached = self.b("new", "--title", "Attached", "--next", "Review", "--pr", "7").stdout.strip()
        waiting = self.b("new", "--title", "Waiting", "--status", "needs_owner", "--kind", "inspect", "--question", "Inspect result").stdout.strip()
        self.b("done", "--repo", "example-game", "--all", "--except", second, "--by", OWNER, "--dry")
        self.assertEqual(self.card(first)["status"], "todo")
        self.b("done", "--repo", "example-game", "--all", "--except", second, "--by", OWNER)
        self.assertEqual([self.card(identifier)["status"] for identifier in (first, second, attached, waiting)], ["done", "todo", "todo", "done"])
        self.assertIn("Only bot", self.b("done", second, "--by", "codex", check=False).stderr)

    def test_stale_cards_archived(self):
        self.hook("start")
        stale = self.b("new", "--title", "Stale", "--next", "Review").stdout.strip()
        waiting = self.b("new", "--title", "Waiting", "--status", "needs_owner", "--kind", "act", "--question", "Create account").stdout.strip()
        attached = self.b("new", "--title", "Attached", "--next", "Review", "--pr", "7").stdout.strip()
        later = {"BOARD_NOW": "2026-12-05T00:00:00+00:00"}
        self.assertIn("stale 1", self.b("archive", env=later).stdout)
        archived = json.loads(next(Path(self.env["BOARD_DIR"]).glob(f"archive/*/*/{stale}.json")).read_text())
        self.assertEqual(archived["status"], "todo")
        self.assertEqual(archived["archived"]["reason"], "Inactive for 14 days")
        self.assertEqual(self.card(waiting)["status"], "needs_owner")
        self.assertEqual(self.card(attached)["pr"], 7)
        self.assertIn(stale, self.b("report", "evening", env=later).stdout)

    def test_health_in_reports(self):
        self.hook("start")
        self.assertIn("Not collected yet", self.b("report", "morning").stdout)
        health = {"checked": "2026-11-19T18:00:00+00:00", "repos": [
            {"repo": "example-maintainer/example-game", "name": "example-game", "light": "red", "line": "Default branch CI is red"},
            {"repo": "example-maintainer/demo-app", "name": "demo-app", "light": "green", "line": "No action needed"}]}
        (Path(self.env["BOARD_DIR"]) / "health.json").write_text(json.dumps(health))
        self.assertIn("[Repository health] 2 repositories", self.b("report", "morning").stdout)
        self.assertNotIn("- green", self.b("report", "morning").stdout)
        self.assertIn("- green demo-app", self.b("report", "health").stdout)
        self.assertIn("not updated for 5 hours", self.b("report", "evening", env={"BOARD_NOW": "2026-11-19T23:00:00+00:00"}).stdout)
        self.b("push")
        self.assertIn("health.json", self.g(self.remote, "ls-tree", "--name-only", "board"))

    def test_owner_required_and_no_sheet_commands(self):
        self.hook("start")
        env = dict(self.env)
        env.pop("SOLOFORGE_OWNER")
        result = run([sys.executable, str(self.tool), "list"], env=env, check=False)
        self.assertNotEqual(result.returncode, 0)
        result = run([sys.executable, str(self.tool), "--owner", OWNER, "list"], env=env)
        self.assertEqual(result.returncode, 0)
        for command in ("export-sheet", "import-sheet"):
            self.assertNotEqual(self.b(command, check=False).returncode, 0)

    def test_task_finish_preserves_decisions_history_and_pause(self):
        self.hook("start")
        identifier = self.b("new", "--title", "Review choice", "--status", "needs_owner", "--kind", "inspect",
                            "--question", "Inspect the result", "--branch", "feature/demo-app").stdout.strip()
        original = self.card(identifier)
        self.b("task-start", "--session", "task-test", "--agent", "echo")
        summary = self.root / "summary.md"
        summary.write_text('```board\n{"status":"needs_owner","question":"Malformed choice"}\n```\n')
        self.b("task-finish", "--summary", str(summary), "--branch", "feature/demo-app", "--agent", "echo")
        current = self.card(identifier)
        self.assertEqual(current["history"][:len(original["history"])], original["history"])
        self.assertEqual(current["decision"], original["decision"])
        self.assertEqual(current["status"], "needs_owner")
        self.assertEqual(len(self.remote_cards()), 1)
        other = self.b("new", "--title", "Active", "--status", "in_progress", "--next", "Review", "--branch", "feature/active").stdout.strip()
        summary.write_text("No board block\n")
        self.b("task-finish", "--summary", str(summary), "--branch", "feature/active", "--agent", "echo", "--result", "paused")
        self.assertEqual(self.card(other)["status"], "blocked")
        self.assertEqual(self.card(other)["blocker"]["ruled_out"], ["No automatic retry was attempted"])


TASK_RULES_TITLE = "## Board handoff"


if __name__ == "__main__":
    unittest.main()
