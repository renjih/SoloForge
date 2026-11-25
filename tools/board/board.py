#!/usr/bin/env python3
"""Validated task cards, session handoffs, and reports on an orphan board branch."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone


STATUSES = ["todo", "in_progress", "blocked", "needs_owner", "done"]
TRANSITIONS = {
    "todo": {"in_progress", "blocked", "needs_owner", "done"},
    "in_progress": {"todo", "blocked", "needs_owner", "done"},
    "blocked": {"todo", "in_progress", "needs_owner", "done"},
    "needs_owner": {"todo", "in_progress", "done"},
    "done": set(),
}
PRIORITIES = ["P0", "P1", "P2", "P3"]
KINDS = ["decide", "inspect", "act"]
TIERS = ["default_after_48h", "wait_for_owner"]
ARCHIVE_DAYS = 14
INJECT_MAX = 12
CHAT = "_chat"


def now():
    return datetime.fromisoformat(os.environ["BOARD_NOW"]) if os.environ.get("BOARD_NOW") else datetime.now(timezone.utc).replace(microsecond=0)


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def owner():
    value = os.environ.get("SOLOFORGE_OWNER", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", value):
        raise ValueError("Set SOLOFORGE_OWNER or pass --owner before the command.")
    return value


def done_actors():
    return {"bot", owner()}


def norm_repo(value):
    value = (value or "").strip().rstrip("/").split("/")[-1].removesuffix(".git").lower()
    if value != CHAT and not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", value):
        raise ValueError("Repository names must be simple names without path components.")
    return value


def board_dir():
    return Path(os.environ.get("BOARD_DIR", Path.home() / ".cache/soloforge/board"))


def git(*args, cwd=None, check=False):
    command = ["git"]
    if os.environ.get("SOLOFORGE_GIT_HOOKS"):
        command += ["-c", "core.hooksPath=" + os.environ["SOLOFORGE_GIT_HOOKS"]]
    result = subprocess.run([*command, *args], cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60, env=dict(os.environ))
    if check and result.returncode:
        raise ValueError("A board Git operation failed.")
    return result


def current_repo():
    if os.environ.get("BOARD_REPO"):
        return norm_repo(os.environ["BOARD_REPO"])
    remote = git("remote", "get-url", "origin")
    return norm_repo(remote.stdout if remote.returncode == 0 else Path.cwd().name)


def session_path():
    result = git("rev-parse", "--absolute-git-dir")
    return Path(result.stdout.strip()) / "board-session.json" if not result.returncode else None


def load_session():
    path = session_path()
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else None
    except (ValueError, OSError):
        return None


def actor(explicit=None):
    return explicit or os.environ.get("BOARD_ACTOR") or (load_session() or {}).get("agent") or "unknown"


def cards_root():
    return board_dir() / "cards"


def all_cards():
    cards = []
    for path in sorted(cards_root().glob("*/*.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(card, dict):
                raise ValueError("Expected a JSON object")
            cards.append(card)
        except (ValueError, OSError) as error:
            cards.append({"id": path.stem, "repo": path.parent.name, "_broken": str(error)})
    return cards


def find_card_soft(identifier):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier or ""):
        raise ValueError("Invalid card identifier.")
    matches = list(cards_root().glob(f"*/{identifier}.json"))
    if len(matches) > 1:
        raise ValueError("Card identifiers must be unique.")
    return json.loads(matches[0].read_text(encoding="utf-8")) if matches else None


def find_card(identifier):
    card = find_card_soft(identifier)
    if card is None:
        raise ValueError("Card not found; use list to inspect available cards.")
    return card


def save(card):
    repo = norm_repo(card["repo"])
    identifier = card["id"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier) or identifier in {".", ".."}:
        raise ValueError("Invalid card identifier.")
    path = cards_root() / repo / f"{identifier}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def opt_label(option):
    return option.split(":", 1)[0].strip()


def validate_card(card):
    if "_broken" in card:
        return ["Invalid card JSON."]
    errors = []
    for key in ("id", "repo", "title", "status", "priority", "updated_at", "updated_by"):
        if not card.get(key):
            errors.append("Missing field: " + key)
    status = card.get("status")
    if status not in STATUSES:
        errors.append("Invalid status.")
    if card.get("priority") not in PRIORITIES:
        errors.append("Invalid priority.")
    if status == "blocked":
        blocker = card.get("blocker") or {}
        if not blocker.get("what"):
            errors.append("Blocked cards require --blocker.")
        if not blocker.get("ruled_out") or not all(isinstance(item, str) and item.strip() for item in blocker["ruled_out"]):
            errors.append("Blocked cards require at least one --ruled-out result.")
    if status == "needs_owner":
        decision = card.get("decision") or {}
        if decision.get("kind") not in KINDS:
            errors.append("Decision kind must be decide, inspect, or act.")
        if not decision.get("question"):
            errors.append("Owner decisions require --question.")
        if decision.get("tier") not in TIERS:
            errors.append("Invalid decision tier.")
        if decision.get("kind") == "decide":
            labels = [opt_label(option) for option in decision.get("options") or []]
            if len(labels) < 2 or len(set(labels)) != len(labels):
                errors.append("Decisions require at least two distinct --option labels.")
            if decision.get("recommended") not in labels:
                errors.append("--recommended must match an option label.")
            if decision.get("tier") == "default_after_48h" and not decision.get("deadline"):
                errors.append("Default decisions require a deadline.")
        elif decision.get("tier") != "wait_for_owner":
            errors.append("Inspect and act decisions must wait_for_owner.")
    if status not in ("done", "needs_owner") and not card.get("next"):
        errors.append("Unfinished cards require a next step: --next.")
    previous = None
    for entry in card.get("history") or []:
        before, after = entry.get("from"), entry.get("to")
        if before is not None and before != after and after not in TRANSITIONS.get(before, set()):
            errors.append("Invalid history transition.")
        if after == "done" and before != "done" and entry.get("by") not in done_actors():
            errors.append("Only bot or the configured owner can mark done.")
        previous = after
    if previous is not None and previous != status:
        errors.append("History does not match the current status; use board.py to update cards.")
    return errors


def apply_change(card, status, by, note=""):
    previous = card.get("status")
    if status and status != previous:
        if previous is not None and status not in TRANSITIONS.get(previous, set()):
            raise ValueError("Invalid transition; done is terminal, use new --reopens to restart.")
        if status == "done" and by not in done_actors():
            raise ValueError("Only bot or the configured owner can mark done; attach a PR instead.")
        if previous == "needs_owner" and not (card.get("decision") or {}).get("answer"):
            raise ValueError("Write --answer before leaving needs_owner.")
        if previous == "blocked" and status != "blocked":
            card["blocker_last"], card["blocker"] = card.get("blocker"), None
        card["status"] = status
    card.update(updated_at=now().isoformat(), updated_by=by,
                session=(load_session() or {}).get("session_id"))
    card.setdefault("history", []).append({"at": card["updated_at"], "by": by,
                                          "from": previous, "to": card["status"], "note": note})


def blank_card(repo, title, priority="P2", assignee=""):
    repo = norm_repo(repo)
    return {"id": f"{repo[:14]}-{now():%m%d}-{uuid.uuid4().hex[:8]}", "repo": repo,
            "title": title, "status": None, "priority": priority, "owner": assignee,
            "branch": "", "pr": None, "url": "", "files": [], "today": "", "state": "",
            "next": "", "blocker": None, "decision": None}


def fill_fields(card, arguments, by):
    for key in ("title", "priority", "branch", "today", "state", "next", "url"):
        value = getattr(arguments, key, None)
        if value:
            card[key] = value
    if getattr(arguments, "assignee", None):
        card["owner"] = arguments.assignee
    if getattr(arguments, "pr", None) is not None:
        card["pr"] = arguments.pr
    if getattr(arguments, "files", None):
        card["files"] = sorted(set(card.get("files") or []) | set(arguments.files))
    if getattr(arguments, "blocker", None) or getattr(arguments, "ruled_out", None):
        blocker = card.get("blocker") or {"what": "", "ruled_out": []}
        blocker["what"] = arguments.blocker or blocker["what"]
        blocker["ruled_out"] += arguments.ruled_out or []
        card["blocker"] = blocker
    if getattr(arguments, "question", None):
        kind = arguments.kind or "decide"
        tier = arguments.tier or ("default_after_48h" if kind == "decide" else "wait_for_owner")
        decision = {"kind": kind, "question": arguments.question, "options": arguments.option or [],
                    "recommended": arguments.recommended, "tier": tier, "asked_at": now().isoformat(),
                    "answer": None, "url": arguments.url or card.get("url", "")}
        if kind == "decide" and tier == "default_after_48h":
            decision["deadline"] = (now() + timedelta(hours=48)).isoformat()
        card["decision"] = decision
    if getattr(arguments, "answer", None):
        card.setdefault("decision", {})
        card["decision"] = {**(card.get("decision") or {}), "answer": arguments.answer,
                            "answered_by": by, "answered_at": now().isoformat()}


def commit_card(card, status, by, note=""):
    apply_change(card, status, by, note)
    errors = validate_card(card)
    if errors:
        raise ValueError("Card was not saved:\n- " + "\n- ".join(errors))
    save(card)


def cmd_new(arguments):
    by = actor(arguments.by)
    card = blank_card(arguments.repo or current_repo(), arguments.title, arguments.priority, arguments.assignee or by)
    if arguments.reopens:
        card["reopens"] = arguments.reopens
    fill_fields(card, arguments, by)
    commit_card(card, arguments.status, by, arguments.note or "Created")
    print(card["id"])


def cmd_set(arguments):
    card = find_card(arguments.id)
    fill_fields(card, arguments, actor(arguments.by))
    commit_card(card, arguments.status, actor(arguments.by), arguments.note or "Updated")
    print(f"{card['id']} -> {card['status']}")


def cmd_list(arguments):
    for card in all_cards():
        if arguments.repo and card.get("repo") != norm_repo(arguments.repo):
            continue
        if arguments.status and card.get("status") != arguments.status:
            continue
        if not arguments.all and card.get("status") == "done" or arguments.no_pr and card.get("pr"):
            continue
        print("\t".join(str(card.get(key, "")) for key in ("id", "repo", "status", "priority", "title")))


def cmd_validate(_arguments):
    invalid = 0
    for card in all_cards():
        errors = validate_card(card)
        if errors:
            invalid += 1
            print(f"{card.get('id')}: " + "; ".join(errors))
    print(f"Validation complete: {invalid} invalid cards")
    return bool(invalid)


def push(message):
    directory = board_dir()
    if not (directory / ".git").exists():
        print("Board data is not a Git checkout; nothing was pushed.", file=sys.stderr)
        return 1
    branch = git("branch", "--show-current", cwd=directory).stdout.strip()
    if branch != "board":
        raise ValueError("Board data must be checked out on the board branch.")
    paths = [path for path in ("cards", "archive", "health.json") if (directory / path).exists()]
    if paths:
        git("add", "-A", "--", *paths, cwd=directory, check=True)
    if git("diff", "--cached", "--quiet", cwd=directory).returncode:
        git("-c", "user.name=SoloForge Board", "-c", "user.email=noreply@example.invalid",
            "commit", "-q", "-m", message, cwd=directory, check=True)
    for _ in range(4):
        remote = git("ls-remote", "--exit-code", "origin", "refs/heads/board", cwd=directory)
        if remote.returncode == 0:
            pulled = git("-c", "user.name=SoloForge Board", "-c", "user.email=noreply@example.invalid",
                         "pull", "-q", "--rebase", "origin", "board", cwd=directory)
            if pulled.returncode:
                git("rebase", "--abort", cwd=directory)
                raise ValueError("Concurrent board edits conflict; local card commits were retained.")
        if not git("push", "-q", "origin", "HEAD:refs/heads/board", cwd=directory).returncode:
            return 0
    raise ValueError("Board push failed; local commits were retained for the next session.")


def expire_one(card):
    decision = card.get("decision") or {}
    if (card.get("status") == "needs_owner" and decision.get("tier") == "default_after_48h"
            and not decision.get("answer") and decision.get("recommended") and decision.get("deadline")
            and parse(decision["deadline"]) <= now()):
        decision.update(answer=decision["recommended"], answered_by="default_after_48h", answered_at=now().isoformat())
        card["decision"] = decision
        card["next"] = card.get("next") or f"Apply {decision['recommended']}: {decision['question']}"
        apply_change(card, "todo", "bot", "No response after 48 hours; applied the recommended option")
        return True
    return False


def cmd_expire(arguments):
    count = 0
    for card in all_cards():
        if "_broken" not in card and expire_one(card):
            if not arguments.dry:
                save(card)
            count += 1
    print(f"Resolved {count} decisions")


def gh_json(path):
    result = subprocess.run(["gh", "api", "--hostname", "github.com", path], capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise ValueError("GitHub state could not be read.")
    return json.loads(result.stdout)


def cmd_sync_merged(_arguments):
    count = 0
    for card in all_cards():
        if "_broken" in card or card.get("status") == "done" or not card.get("pr") or card["repo"] == CHAT:
            continue
        try:
            pr = gh_json(f"repos/{owner()}/{card['repo']}/pulls/{card['pr']}")
        except (ValueError, OSError, subprocess.TimeoutExpired):
            print(f"Could not read PR for {card['id']}", file=sys.stderr)
            continue
        if pr.get("merged"):
            if card["status"] == "needs_owner":
                card["decision"]["answer"] = card["decision"].get("answer") or "PR merged"
            apply_change(card, "done", "bot", "PR merged")
        elif pr.get("state") == "closed" and card["status"] not in ("todo", "needs_owner"):
            card["pr_closed"] = card.pop("pr")
            card["next"] = card.get("next") or "Reconsider work after the PR closed without merging"
            apply_change(card, "todo", "bot", "PR closed without merging")
        else:
            continue
        save(card)
        count += 1
    print(f"Synced {count} cards")


def cmd_merged(arguments):
    for card in all_cards():
        if card.get("repo") == norm_repo(arguments.repo) and card.get("pr") == arguments.pr and card.get("status") != "done":
            if card["status"] == "needs_owner":
                card["decision"]["answer"] = card["decision"].get("answer") or "PR merged"
            commit_card(card, "done", "bot", "PR merged")


def cmd_archive(arguments):
    cutoff = now() - timedelta(days=arguments.days)
    count = stale = 0
    for card in all_cards():
        if "_broken" in card or not parse(card.get("updated_at")) or parse(card["updated_at"]) >= cutoff:
            continue
        if card["status"] != "done" and (card["status"] == "needs_owner" or card.get("pr")):
            continue
        source = cards_root() / card["repo"] / f"{card['id']}.json"
        if card["status"] != "done":
            apply_change(card, None, "bot", f"Archived after {arguments.days} inactive days")
            card["archived"] = {"at": card["updated_at"], "reason": f"Inactive for {arguments.days} days"}
            stale += 1
        target = board_dir() / "archive" / f"{parse(card['updated_at']):%Y-%m}" / card["repo"] / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        source.unlink()
        count += 1
    print(f"Archived {count} cards (stale {stale})")


def cmd_done(arguments):
    by = actor(arguments.by)
    if by not in done_actors():
        raise ValueError("Only bot or the configured owner can run done; pass --by OWNER.")
    if arguments.ids:
        cards = [find_card(identifier) for identifier in arguments.ids]
    elif arguments.all and arguments.repo:
        cards = [card for card in all_cards() if card.get("repo") == norm_repo(arguments.repo) and not card.get("pr")]
    else:
        raise ValueError("Provide card IDs or --repo REPO --all.")
    count = 0
    for card in cards:
        if card["id"] in (arguments.exclude or []) or card.get("status") == "done":
            continue
        if card.get("pr") and not arguments.force:
            print(f"Skipped {card['id']}: its PR must merge first")
            continue
        if arguments.dry:
            print("Would complete: " + card["id"])
        else:
            if card["status"] == "needs_owner" and not card["decision"].get("answer"):
                card["decision"].update(answer="Owner marked done", answered_by=by, answered_at=now().isoformat())
            commit_card(card, "done", by, arguments.note or "Owner marked done")
        count += 1
    print(f"{'Would complete' if arguments.dry else 'Completed'} {count} cards")


def priority(card):
    return PRIORITIES.index(card.get("priority")) if card.get("priority") in PRIORITIES else 9


def load_live():
    cards = [card for card in all_cards() if "_broken" not in card]
    for card in cards:
        expire_one(card)
    return cards


def health_lines(full=False):
    path = board_dir() / "health.json"
    if not path.exists():
        return ["[Repository health] Not collected yet"]
    health = json.loads(path.read_text(encoding="utf-8"))
    age = max(0, int((now() - parse(health["checked"])).total_seconds() // 60))
    rows = health.get("repos") or []
    lines = [f"[Repository health] {len(rows)} repositories; checked {age} minutes ago"]
    if age > 180:
        lines.append(f"Stale data: not updated for {age // 60} hours")
    order = {"red": 0, "yellow": 1, "green": 2}
    for row in sorted(rows, key=lambda row: (order.get(row.get("light"), 3), row["repo"])):
        if full or row.get("light") != "green":
            lines.append(f"- {row.get('light')} {row.get('name', row['repo'])}: {row.get('line')}")
    return lines


def cmd_report(arguments):
    if arguments.kind == "health":
        print("\n".join(health_lines(full=True)))
        return
    cards = load_live()
    live = [card for card in cards if card["status"] != "done"]
    since = now() - timedelta(hours=arguments.hours)
    completed = [card for card in cards if card["status"] == "done" and parse(card["updated_at"]) >= since]
    running = [card for card in live if card["status"] in ("todo", "in_progress")]
    title = "Evening wrap" if arguments.kind == "evening" else "Morning brief"
    lines = [f"[Board {title} {now():%Y-%m-%d %H:%M}] Completed {len(completed)}; active {len(running)}"]
    asks = [card for card in live if card["status"] == "needs_owner"]
    decisions = sorted([card for card in asks if card["decision"]["kind"] == "decide"],
                       key=lambda card: (card["decision"]["tier"] != "wait_for_owner", priority(card), card["repo"], card["id"]))
    limit = min(5, arguments.max)
    if decisions:
        lines.append(f"[Decisions] {len(decisions)}")
        for card in decisions[:limit]:
            decision = card["decision"]
            tail = "Waits for the owner" if decision["tier"] == "wait_for_owner" else f"Recommended {decision['recommended']}; defaults after 48 hours"
            lines.append(f"- {card['repo']}: {decision['question']} | {tail}")
            lines.append("  " + " / ".join(decision["options"]))
        if len(decisions) > limit:
            lines.append(f"Another {len(decisions) - limit} decisions remain")
    for kind, label in (("inspect", "Owner inspection"), ("act", "Owner actions")):
        selected = [card for card in asks if card["decision"]["kind"] == kind]
        if not selected:
            continue
        lines.append(f"[{label}] {len(selected)}")
        if kind == "act" and arguments.kind == "evening" and now().isoweekday() != 7:
            lines.append("Batch owner actions on Sunday")
        else:
            lines += [f"- {card['repo']}: {card['decision']['question']}" for card in selected[:5]]
    stuck = [card for card in live if card["status"] == "blocked"]
    automatic = [card for card in live if card.get("auto")]
    broken = [card for card in all_cards() if validate_card(card)]
    if stuck or automatic or broken:
        lines.append(f"[Problems] {len(stuck) + len(automatic) + len(broken)}")
        lines += [f"- {card['repo']}: {card['title']} | {card['blocker']['what']}" for card in stuck[:5]]
        lines += [f"- {card['repo']}: {card['title']} | Missing handoff" for card in automatic[:2]]
        if broken:
            lines.append(f"Invalid cards: {len(broken)}")
    archived = []
    for path in (board_dir() / "archive").glob("*/*/*.json"):
        card = json.loads(path.read_text(encoding="utf-8"))
        stamp = parse((card.get("archived") or {}).get("at"))
        if stamp and stamp >= since:
            archived.append(card)
    if archived:
        lines.append(f"[Archived after {ARCHIVE_DAYS} inactive days] {len(archived)}")
        lines += [f"- {card['title']} `{card['id']}`; restore with new --reopens" for card in archived[:5]]
    defaulted = [card for card in cards if (card.get("decision") or {}).get("answered_by") == "default_after_48h"
                 and parse(card["decision"]["answered_at"]) >= since]
    if defaulted:
        lines.append(f"[Defaulted decisions] {len(defaulted)}")
    lines += health_lines()
    if arguments.kind == "morning":
        lines.append("[First steps today]")
        for repo in sorted({card["repo"] for card in running}):
            selected = sorted([card for card in running if card["repo"] == repo],
                              key=lambda card: (card["status"] != "in_progress", priority(card), card["id"]))
            lines += [f"- {repo}: {card['title']} -> {card['next']}" for card in selected[:2]]
    print("\n".join(lines))


def cmd_handoff(arguments):
    repo = norm_repo(arguments.repo)
    print(f"# {repo} handoff {now():%Y-%m-%d}\n\nGenerated from the board branch.\n")
    for card in sorted([card for card in load_live() if card["repo"] == repo and card["status"] != "done"], key=priority):
        blocker = card.get("blocker") or {}
        print(f"## {card['title']} ({card['status']}, {card['priority']}) `{card['id']}`\n"
              f"- Today: {card.get('today', '')}\n- State: {card.get('state', '')}\n"
              f"- Blocker: {blocker.get('what', '')}\n- Ruled out: {'; '.join(blocker.get('ruled_out') or [])}\n"
              f"- Next: {card.get('next', '')}\n- Files: {', '.join(card.get('files') or [])}\n"
              f"- Branch: {card.get('branch', '')}; PR: {card.get('pr') or ''}\n")


def cmd_health(arguments):
    import health
    if arguments.projects:
        document = json.loads(Path(arguments.projects).read_text(encoding="utf-8"))
        projects = document["projects"] if isinstance(document, dict) else document
    else:
        projects = [{"repo": f"{owner()}/{current_repo()}"}]
    result = health.collect(projects, by=arguments.by)
    if arguments.dry:
        print(json.dumps(result, indent=2))
    else:
        board_dir().mkdir(parents=True, exist_ok=True)
        (board_dir() / "health.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def fingerprint():
    parts = [git("rev-parse", "HEAD").stdout, git("status", "--porcelain").stdout, git("diff", "HEAD").stdout]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def changed_files(session):
    files = set(git("diff", "--name-only", "-z", session.get("head") or "HEAD").stdout.rstrip("\0").split("\0"))
    files.update(git("ls-files", "--others", "--exclude-standard", "-z").stdout.rstrip("\0").split("\0"))
    return sorted(files - {""})


def version(card):
    return f"{card.get('updated_at')}#{len(card.get('history') or [])}#{card.get('_broken', '')}"


def seen_versions(repo):
    return {card["id"]: version(card) for card in all_cards() if card.get("repo") in (repo, CHAT)}


def start_session(agent, identifier):
    repo = current_repo()
    path = session_path()
    if path:
        path.write_text(json.dumps({"agent": agent, "session_id": identifier, "repo": repo,
                                  "started_at": now().isoformat(), "head": git("rev-parse", "HEAD").stdout.strip(),
                                  "fp": fingerprint(), "seen": seen_versions(repo)}), encoding="utf-8")
    return repo


def board_context(repo):
    order = {"in_progress": 0, "blocked": 1, "todo": 2, "needs_owner": 3}
    cards = sorted([card for card in load_live() if card.get("repo") == repo and card.get("status") != "done"],
                   key=lambda card: (order.get(card["status"], 9), priority(card), card["id"]))
    lines = [f"[Board] Repository {repo}: {len(cards)} unfinished cards"]
    for card in cards[:INJECT_MAX]:
        tail = ""
        if card["status"] == "blocked":
            tail = f" | Blocker: {card['blocker']['what']} | Ruled out: {'; '.join(card['blocker']['ruled_out'])}"
        elif card["status"] == "needs_owner":
            tail = f" | Owner: {card['decision']['question']}"
        lines.append(f"- {card['id']} [{card['status']}/{card['priority']}] {card['title']} -> Next: {card.get('next', '')}{tail}")
        decision = card.get("decision") or {}
        if decision.get("answer") and card["status"] != "needs_owner":
            lines.append(f"  Decided: {decision['answer']}; follow this answer")
    if len(cards) > INJECT_MAX:
        lines.append(f"Another {len(cards) - INJECT_MAX} cards; use list --repo {repo}")
    return "\n".join(lines)


def hook_input(agent):
    if agent != "claude" or sys.stdin.isatty():
        return {}
    try:
        value = json.loads(sys.stdin.read() or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def cmd_hook_start(arguments):
    payload = hook_input(arguments.agent)
    repo = start_session(arguments.agent, payload.get("session_id") or uuid.uuid4().hex)
    push("board: retry pending local card commits")
    text = board_context(repo)
    if arguments.agent == "claude":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    else:
        print(text)


def touched_since(session):
    return [card for card in all_cards() if card.get("repo") in (session["repo"], CHAT)
            and session.get("seen", {}).get(card["id"]) != version(card)
            and (card.get("session") == session.get("session_id") or "_broken" in card)]


def carried_card(session):
    for identifier in reversed(session.get("carded") or []):
        card = find_card_soft(identifier)
        if card and card.get("status") != "done" and "_broken" not in card:
            return card
    return None


def append_commits(card, session):
    previous = session.get("last_head") or session.get("head")
    if not previous or git("cat-file", "-e", previous + "^{commit}").returncode:
        return
    commits = git("log", "--format=%h %s", f"{previous}..HEAD").stdout.splitlines()
    if commits:
        card["today"] = ((card.get("today") or "") + "; New commits: " + ", ".join(commits[:5]))[-1200:]
        card["files"] = sorted(set(card.get("files") or []) | set(git("diff", "--name-only", previous, "HEAD").stdout.splitlines()))
        commit_card(card, None, session["agent"], f"Stop hook appended {len(commits)} new commits")


def auto_card(session, files, reason, summary=""):
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    card = blank_card(session["repo"], f"[Missing handoff] {session['agent']} changes on {branch}", assignee=session["agent"])
    card.update(branch=branch, files=files, auto=True, today=(summary or reason)[:600],
                state="Handoff was missing or invalid", next="Inspect these changes and complete this card")
    commit_card(card, "in_progress", session["agent"], reason)
    return card


def cmd_hook_stop(arguments):
    payload = hook_input(arguments.agent)
    retried = bool(payload.get("stop_hook_active")) or arguments.final
    session = load_session()
    if not session:
        return 0
    touched = touched_since(session)
    invalid = {card["id"]: validate_card(card) for card in touched if validate_card(card)}
    worked = fingerprint() != session.get("fp")
    carried = None if touched else carried_card(session)
    problem = None
    if invalid:
        problem = "Invalid cards: " + "; ".join(f"{identifier}: {', '.join(errors)}" for identifier, errors in invalid.items())
    elif worked and not touched and not carried:
        problem = "Files changed without a session handoff. Update or create a card with today, state, and next."
    if problem and not retried:
        if arguments.agent == "claude":
            print(json.dumps({"decision": "block", "reason": "[Board stop check] " + problem}))
            return 0
        print(problem)
        return 3
    if carried and worked:
        append_commits(carried, session)
    elif retried and invalid:
        fallback = auto_card(session, changed_files(session), "Invalid handoff after the first Stop request")
        rejected = []
        for path in cards_root().glob("*/*.json"):
            if path.stem in invalid:
                rejected.append({"id": path.stem, "raw": path.read_text(encoding="utf-8")})
                path.unlink()
        fallback["rejected_handoffs"] = rejected
        save(fallback)
        touched = [fallback]
    elif retried and worked and not touched:
        touched = [auto_card(session, changed_files(session), "Stop already requested a handoff; created a fallback card")]
    identifiers = [card["id"] for card in touched if not validate_card(card)]
    session["carded"] = list(dict.fromkeys((session.get("carded") or []) + identifiers))
    session.update(started_at=now().isoformat(), fp=fingerprint(),
                   last_head=git("rev-parse", "HEAD").stdout.strip(), seen=seen_versions(session["repo"]))
    session_path().write_text(json.dumps(session), encoding="utf-8")
    push(f"board: {session['repo']} {session['agent']} handoff")


TASK_RULES = """## Board handoff
Write one final fenced `board` JSON block in the summary. The dispatcher writes it.
Fields: id (existing card), title (new card), status (todo, in_progress, blocked,
needs_owner), today, state, next. Never mark done: attach the PR for merge sync.
Blocked cards need blocker and ruled_out (a list of failed approaches).
Owner decisions need question, options (at least two labeled strings), recommended;
use tier wait_for_owner for decisions requiring explicit approval.
Decide routine implementation details within the assigned scope.
"""


def parse_board_block(text):
    blocks = re.findall(r"```board\s*\n(.*?)```", text or "", re.S)
    try:
        value = json.loads(blocks[-1]) if blocks else None
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def strip_board_block(text):
    return re.sub(r"\n*```board\s*\n.*?```\s*", "\n", text or "", flags=re.S).rstrip() + "\n"


def cmd_strip(_arguments):
    sys.stdout.write(strip_board_block(sys.stdin.read()))


def cmd_task_start(arguments):
    repo = start_session(arguments.agent, arguments.session or uuid.uuid4().hex)
    print(board_context(repo) + "\n\n" + TASK_RULES)


def cmd_task_finish(arguments):
    session = load_session() or {"agent": arguments.agent, "repo": current_repo(),
                                "session_id": uuid.uuid4().hex, "head": ""}
    summary_path = Path(arguments.summary)
    text = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    block = parse_board_block(text)
    files = changed_files(session) if session.get("head") else []
    branch = arguments.branch or git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    card = None
    if block and block.get("id"):
        card = find_card_soft(block["id"])
        if card and card.get("repo") != session["repo"]:
            raise ValueError("A task cannot update another repository's card.")
    if card is None:
        card = next((item for item in all_cards() if item.get("repo") == session["repo"]
                     and item.get("branch") == branch and item.get("status") not in ("done", None)), None)
    original = copy.deepcopy(card)
    pending_owner = card is not None and card.get("status") == "needs_owner" and not (card.get("decision") or {}).get("answer")
    if card is None and not block and not files and arguments.result not in ("paused", "failed"):
        print("No changed files or board block; no fallback card needed")
        return 0
    fallback = not block
    if card is None:
        card = blank_card(session["repo"], (block or {}).get("title") or f"[Missing handoff] {arguments.agent}: {branch}", assignee=arguments.agent)
    if block:
        for key in ("title", "today", "state", "next"):
            if block.get(key):
                card[key] = block[key]
    card.update(branch=branch, files=sorted(set(card.get("files") or []) | set(files)))
    if arguments.pr:
        card["pr"] = arguments.pr
    status = (block or {}).get("status") or "in_progress"
    if status == "done":
        status = "in_progress"
    if status == "blocked":
        card["blocker"] = {"what": block.get("blocker") or "", "ruled_out": block.get("ruled_out") or []}
    if status == "needs_owner" and not pending_owner:
        kind = block.get("kind", "decide")
        tier = block.get("tier", "default_after_48h" if kind == "decide" else "wait_for_owner")
        card["decision"] = {"kind": kind, "question": block.get("question") or "", "options": block.get("options") or [],
                            "recommended": block.get("recommended"), "tier": tier,
                            "asked_at": now().isoformat(), "answer": None}
        if tier == "default_after_48h":
            card["decision"]["deadline"] = (now() + timedelta(hours=48)).isoformat()
    if arguments.result in ("paused", "failed"):
        status = "blocked"
        card["blocker"] = {"what": "Dispatcher paused" if arguments.result == "paused" else "Dispatcher failed",
                           "ruled_out": ["No automatic retry was attempted"]}
        card["next"] = "Inspect the task result and resume with a new authorized comment"
    elif arguments.result == "publication_unknown":
        status = "in_progress"
        card["next"] = "Inspect the remote branch and pull request before resuming"
    if pending_owner:
        status = "needs_owner"
    card["next"] = card.get("next") or "Review the branch and attached PR"
    if fallback:
        card.update(auto=True, today=strip_board_block(text).strip()[:600] or "Summary did not contain a board block")
    try:
        commit_card(card, status, arguments.agent, "Task handoff")
    except (ValueError, KeyError, TypeError):
        fallback = True
        if original is not None and not validate_card(original):
            card = original
            card.update(auto=True, today=strip_board_block(text).strip()[:600],
                        state="Invalid board block; previous valid state and decision preserved")
            if arguments.pr:
                card["pr"] = arguments.pr
            commit_card(card, None, arguments.agent, "Invalid task handoff; preserved previous state without rerunning the adapter")
        else:
            card = blank_card(session["repo"], f"[Missing handoff] {arguments.agent}: {branch}", assignee=arguments.agent)
            card.update(branch=branch, pr=arguments.pr, files=files, auto=True,
                        next="Inspect the task changes and repair the handoff",
                        today=strip_board_block(text).strip()[:600], state="Invalid board block; fallback recorded")
            commit_card(card, "in_progress", arguments.agent, "Invalid task handoff; fallback recorded without rerunning the adapter")
    push(f"board: {session['repo']} {arguments.agent} task handoff")
    print(f"{'Fallback card' if fallback else 'Board'}: {card['id']} -> {card['status']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", help="GitHub owner; alternatively set SOLOFORGE_OWNER")
    commands = parser.add_subparsers(dest="command", required=True)

    def card_arguments(command, new=False):
        command.add_argument("--title", required=new)
        command.add_argument("--status", choices=STATUSES, default="todo" if new else None)
        command.add_argument("--priority", choices=PRIORITIES, default="P2" if new else None)
        for key in ("assignee", "branch", "url", "today", "state", "next", "blocker", "question", "recommended", "answer", "note", "by"):
            command.add_argument("--" + key)
        command.add_argument("--pr", type=int)
        command.add_argument("--files", nargs="*")
        command.add_argument("--ruled-out", action="append")
        command.add_argument("--option", action="append")
        command.add_argument("--kind", choices=KINDS)
        command.add_argument("--tier", choices=TIERS)

    command = commands.add_parser("new")
    card_arguments(command, True)
    command.add_argument("--repo")
    command.add_argument("--reopens")
    command.set_defaults(function=cmd_new)
    command = commands.add_parser("set")
    command.add_argument("id")
    card_arguments(command)
    command.set_defaults(function=cmd_set)
    command = commands.add_parser("list")
    command.add_argument("--repo")
    command.add_argument("--status", choices=STATUSES)
    command.add_argument("--all", action="store_true")
    command.add_argument("--no-pr", action="store_true")
    command.set_defaults(function=cmd_list)
    command = commands.add_parser("done")
    command.add_argument("ids", nargs="*")
    command.add_argument("--repo")
    command.add_argument("--all", action="store_true")
    command.add_argument("--except", dest="exclude", nargs="*")
    command.add_argument("--force", action="store_true")
    command.add_argument("--dry", action="store_true")
    command.add_argument("--note")
    command.add_argument("--by")
    command.set_defaults(function=cmd_done)
    command = commands.add_parser("show")
    command.add_argument("id")
    command.set_defaults(function=lambda args: print(json.dumps(find_card(args.id), indent=2)))
    commands.add_parser("validate").set_defaults(function=cmd_validate)
    command = commands.add_parser("push")
    command.add_argument("-m", "--message")
    command.set_defaults(function=lambda args: push(args.message or "board: update"))
    command = commands.add_parser("expire")
    command.add_argument("--dry", action="store_true")
    command.set_defaults(function=cmd_expire)
    commands.add_parser("sync-merged").set_defaults(function=cmd_sync_merged)
    command = commands.add_parser("merged")
    command.add_argument("--repo", required=True)
    command.add_argument("--pr", required=True, type=int)
    command.set_defaults(function=cmd_merged)
    command = commands.add_parser("archive")
    command.add_argument("--days", type=int, default=ARCHIVE_DAYS)
    command.set_defaults(function=cmd_archive)
    command = commands.add_parser("report")
    command.add_argument("kind", choices=("morning", "evening", "health"))
    command.add_argument("--max", type=int, default=5)
    command.add_argument("--hours", type=int, default=24)
    command.set_defaults(function=cmd_report)
    command = commands.add_parser("health")
    command.add_argument("--projects")
    command.add_argument("--by", default="local")
    command.add_argument("--dry", action="store_true")
    command.set_defaults(function=cmd_health)
    command = commands.add_parser("handoff")
    command.add_argument("--repo", required=True)
    command.set_defaults(function=cmd_handoff)
    for name, function in (("hook-start", cmd_hook_start), ("hook-stop", cmd_hook_stop)):
        command = commands.add_parser(name)
        command.add_argument("--agent", default="claude")
        if name == "hook-stop":
            command.add_argument("--final", action="store_true")
        command.set_defaults(function=function)
    for name in ("task-start", "codex-start"):
        command = commands.add_parser(name)
        command.add_argument("--session")
        command.add_argument("--agent", default="codex")
        command.set_defaults(function=cmd_task_start)
    for name in ("task-finish", "codex-finish"):
        command = commands.add_parser(name)
        command.add_argument("--summary", required=True)
        command.add_argument("--branch")
        command.add_argument("--pr", type=int)
        command.add_argument("--agent", default="codex")
        command.add_argument("--result", choices=("done", "no_change", "paused", "failed", "publication_unknown"), default="done")
        command.set_defaults(function=cmd_task_finish)
    commands.add_parser("strip").set_defaults(function=cmd_strip)
    arguments = parser.parse_args()
    if arguments.owner:
        os.environ["SOLOFORGE_OWNER"] = arguments.owner
    try:
        owner()
        return arguments.function(arguments) or 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
