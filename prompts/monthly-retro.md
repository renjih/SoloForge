# Monthly retrospective

Inputs: an explicit repository list, the previous thirty days of project history,
and the previous retrospective. Follow the [project rules](../rules/AI-PROJECT-RULES.md)
and use the [task-card format](../rules/REFERENCE.md#appendix-d-task-card-template)
for follow-up work.

1. Gather verified mistake records, repeated review rounds, reverts, rework,
   escalated blockers, and PRs closed without merging. Skip repositories without
   changes and state where evidence was unavailable.
2. Group findings by common root cause, not by symptom or file type. Give each
   group an occurrence count, representative PRs, and the existing rule, if any.
   Open every cited item and verify that it supports the proposed cause.
3. Explain why an existing rule failed: a weak check, an unenforced manual rule,
   missing task context, or another demonstrated reason. Keep one-off events
   visible without creating a new rule for each.
4. For causes observed at least twice, prefer one prevention method in this order:
   an automated architecture rule, a meaningful regression test, a short project
   instruction, then a reusable multi-step skill. Ask the designated architect
   to review new architectural constraints before proposing them.
5. Remove instructions that automation already enforces, that an explicit later
   decision superseded, or that duplicate another source. For a prose-rule PR,
   remove at least as many lines as you add; otherwise propose the change as a
   decision instead. Automated checks and regression tests are assessed by their
   coverage and behavior, rather than this prose line count.
6. Write a retrospective with the verified causes, representative evidence,
   proposed prevention, obsolete rules, unresolved uncertainty, and next owner.
   Compare the outcome of last month's changes. Keep cross-project policy
   changes pending explicit maintainer approval.

Do not modify implementation code, merge PRs, or push to the default branch.
Prepare focused reviewable follow-up tasks. Update the board so the next review
can find the remaining work without reconstructing this report.
