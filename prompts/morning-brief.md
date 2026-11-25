# Morning brief

Read the current `board` branch first. Use the [board rules](../rules/BOARD.md)
and [board tools](../tools/board/board.py) to produce a read-only starting point
for today's work. The board is the handoff source.

1. Run `report morning --max 5` for the configured owner and data checkout.
   Read unfinished cards, blocker details, approaches already ruled out, owner
   answers, and linked PRs. State which board revision was read.
2. Lead with urgent maintainer actions or decisions, with links. Keep the list
   to at most five items. Carry unanswered explicit-approval requests forward;
   do not treat elapsed time as new permission.
3. For each active project, state the current result, the blocker if any, the
   first concrete next step, and who should take it. Follow recorded owner
   decisions without reopening settled choices.
4. Include repository-health exceptions and the time of the last health update.
   Label stale data and unavailable sources clearly. Distinguish work merged
   since the last handoff from work still awaiting review.
5. Suggest a short work order from the cards' priorities and dependencies.
   Do not start tasks, change status, publish comments, or merge PRs while
   generating the brief.

Keep the brief in plain English and link to evidence. If the board is unavailable,
state that limitation and preserve a concrete
next step for restoring the handoff source.
