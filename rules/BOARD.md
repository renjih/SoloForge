# Task board and handoff

The board records the current handoff for each task. Read it before starting
work, update it when the situation changes, and generate summaries from it.
Use the [board CLI](../tools/board/board.py) rather than editing card JSON by hand.

## Storage and ownership

Keep tools in the reviewed default branch and data on the separate orphan branch
`board`. The data branch has no code history and does not enter normal feature
PRs. Store one card per file at `cards/<repository>/<id>.json`; unrelated cards
can be updated independently. Archive files under `archive/` without changing
their recorded status. Use fictional repository names in examples, such as
`example-game` and `demo-app`.

The CLI requires global `--owner YOUR_ORG` or `SOLOFORGE_OWNER`. There is no
built-in owner. `BOARD_DIR` selects the local data checkout, `BOARD_REPO` selects
the current repository short name, and `BOARD_ACTOR` identifies the writer.
Repository ownership and the card's agent assignee are separate values.

```bash
export SOLOFORGE_OWNER=YOUR_ORG
export BOARD_REPO=example-game
export BOARD_DIR=/tmp/soloforge-board
python3 tools/board/board.py list --repo example-game
python3 tools/board/board.py new --repo example-game --title "Fix parser recovery" \
  --assignee codex --next "Reproduce the failing input"
```

Each card records its title, assignee, status, priority, branch or PR, work done,
current state, next step, and any blocker or owner decision. Keep the next step
specific enough that another agent can resume without reconstructing the chat.

## Five states

| State | Meaning | Required handoff |
| --- | --- | --- |
| `todo` | Ready to be claimed. | A concrete next step. |
| `in_progress` | An agent is working or a PR awaits merge. | Current state, next step, and branch or PR when available. |
| `blocked` | Work cannot advance. | The blocker, at least one approach already ruled out, and the next step. |
| `needs_owner` | A decision or action requires the owner. | A question; decisions also give options and a recommendation. |
| `done` | The task is finished. | Completion recorded by the configured owner or merge automation. |

An implementer links its PR and leaves the card `in_progress`. The maintenance
job marks merged PRs `done`; a PR closed without merging returns to `todo`.
Only the owner or bot may complete a card. `done` is terminal: use a new card
with `--reopens <id>` when work needs to resume.

An owner request has kind `decide`, `inspect`, or `act`. `decide` needs at least
two options and a recommendation. `inspect` and `act` require an explicit owner
response. Use tier `wait_for_owner` for spending, credentials, permissions,
destructive changes, model changes, and other decisions requiring approval.
Use `default_after_48h` only when the owner already authorized a reversible
default. Record the answer before leaving `needs_owner`; expiry never grants
new permission for an action that requires explicit approval.

## Session and Stop hooks

At session start, `hook-start` reads the board and records the session's starting
tree. Inject at most twelve unfinished cards, prioritizing active work and
blockers. The project instructions remain in [AGENTS.md](../AGENTS.md).

At Stop, `hook-stop` checks this session's handoff. An unchanged tree can stop.
Changed files need a valid card written by this session. If the handoff is
missing, block once with the missing fields; the next Stop creates a fallback
handoff and releases the session. Do not run the provider again to repair a
summary. A valid unfinished card from an earlier turn in the same session can
carry the handoff forward; append new commit details when needed. Another
session's card or a completed card does not satisfy this check.

If the board cannot be reached, preserve the handoff fields in the PR description
or final summary and report the missing board update. The board must not hide a
failed implementation or consume another provider attempt.

The dispatcher exposes `pre_task` and `post_task` hooks. `task-start` injects
current cards into the instruction file; `task-finish` reads the final summary,
updates the card, attaches the branch and PR, and pushes board data. A structured
`board` JSON block in a summary can supply the handoff; a missing or invalid block
produces a fallback card without another provider run. See the
[dispatcher hook contract](../docs/dispatcher.md#trusted-task-hooks).

The [dispatch template](../templates/dispatch.yml) enables the trusted
[board integration](../tools/board/dispatch_hook.py) with these inputs:

```yaml
pre_task: 'python3 "$SF_ACTION_ROOT/tools/board/dispatch_hook.py" pre'
post_task: 'python3 "$SF_ACTION_ROOT/tools/board/dispatch_hook.py" post'
```

It writes board data to the caller repository's `board` branch and needs the
caller's Contents write permission. The provider does not receive the GitHub
token. If the task deadline expires, the dispatcher skips the post-hook; inspect
the active card and final run comment before resuming.

The [hook installer](../tools/board/install.py) copies this block into a project's
implementer instructions:

<!-- board:begin -->
## Board handoff

1. Read the board context at session start. Claim an unfinished card or create
   one before editing. Use the configured `board.py` CLI with global `--owner`
   or `SOLOFORGE_OWNER`; there is no default repository owner.
2. Update the card when the situation changes. Record `today`, `state`, `next`,
   changed files, branch, and PR. A `blocked` card needs the blocker and at least
   one approach already ruled out. A `needs_owner` card needs a question and,
   for a decision, options and a recommendation. Use `wait_for_owner` whenever
   explicit approval is required, and follow answers already recorded.
3. Implementers never mark cards `done`. Attach the PR and leave the card
   `in_progress`; merge automation or the configured owner records completion.
   Do not edit card JSON directly or bypass its state validation.
4. Before Stop, write a valid handoff for this session. The hook asks once for a
   missing or invalid handoff, then records a fallback without rerunning the
   provider. If the board is unavailable, preserve the handoff in the final
   summary or PR description and report the missing update.
5. In a dispatched task, finish with one fenced `board` JSON block containing
   `id` or `title`, `status`, `today`, `state`, and `next`; add `blocker` and
   `ruled_out` for blocked work, or the owner-decision fields when needed. The
   dispatcher records it and links the PR. Keep credentials and personal data
   out of cards and summaries.
<!-- board:end -->

## Maintenance and reports

The [maintenance workflow](../.github/workflows/board.yml) runs every two hours
and on manual request, using the reviewed tools on a hosted runner. Its
[maintenance helper](../tools/board/maintain.py) initializes or checks out the
orphan data branch, expires authorized defaults, synchronizes PR state, archives
eligible cards, refreshes repository health, validates the data, and pushes.
Credentials travel through environment variables, never repository URLs.

Archive completed cards after fourteen days. Also archive cards that have no PR
and have not changed for fourteen days, except `needs_owner` cards. Preserve
their status, record stale-card archive metadata and history, and leave linked
open PRs and pending owner decisions visible. Reopen by reference instead of rewriting the
old record.

Reports are read-only views of the board. `report morning` summarizes unfinished
work and the next step; `report evening --max 5` prioritizes at most five owner
decisions; `report health` shows repository health. Identify stale health data
explicitly. Use the [morning brief](../prompts/morning-brief.md) and
[evening wrap](../prompts/evening-wrap.md) prompts for plain-English handoff.
