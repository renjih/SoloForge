# Evening wrap

Read the current `board` branch using the [board tools](../tools/board/board.py)
and [board rules](../rules/BOARD.md). Produce a read-only end-of-day handoff;
do not search unrelated chats or invent missing progress.

1. Run `report evening --max 5` for the configured owner and board checkout.
   Read changed cards, unresolved blockers, merged work, and today's archive
   records. State the board revision and freshness of repository-health data.
2. Lead with at most five maintainer decisions or actions, ordered by urgency.
   For each give the question, available options, recommendation, consequence,
   and card or PR link. Separate explicit-approval requests from reversible
   defaults already authorized by the owner.
3. Summarize completed work, current blockers and approaches already ruled out,
   and the first next step for each active project. Report stale or missing data
   plainly instead of presenting it as current.
4. List eligible cards that were archived and any work whose handoff is missing.
   Do not mark work complete or archive cards while preparing this report.
5. End with the next owner and next action. Keep routine implementation details
   in the linked cards so the maintainer can scan the handoff quickly.

Do not modify the board, dispatch more work, or merge PRs. Do not
create extra questions merely to fill the five-decision limit.
