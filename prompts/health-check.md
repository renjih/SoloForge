# Project health review

Inputs: target repository, its documented setup and check commands, the previous
health report, and the approved model pair. Read the
[project rules](../rules/AI-PROJECT-RULES.md), [reference](../rules/REFERENCE.md),
and [model policy](../rules/MODELS.md) before reviewing.

1. Read the current architecture, invariants, and design debt. Run the documented
   project checks on the current default-branch commit. Record the command,
   result, checked SHA, and any missing configuration. A failing baseline is a
   blocker; report it and do not dispatch new cleanup work.
2. Review pending cleanup and previous health-report PRs first. Preserve their
   history, remove merge eligibility while review is pending, and recheck any new
   head commit. A review record for an older commit is historical evidence only.
3. Perform a broader review after three merged changes, completion of a plan, or
   fourteen days with new merges. If none applies, record the routine check
   result and the reason a broader review is not due.
4. Compare size, complexity, duplication, unused code, failure handling, and
   behavioral baselines with the previous report. Explain regressions using
   concrete files and reproducible evidence. Reconcile the documented layers and
   design debt with the code.
5. Ask an independent reviewer from a different provider to verify the likely
   defects. Give it the architecture and affected source, not a predetermined
   verdict. Verify every finding and record disagreements or missing evidence.
6. Write a report and any architecture-document corrections on a report branch.
   Do not change implementation code during the review. Propose cleanup as a
   separate task with an explicit deletion list and proof that behavior remains
   unchanged. Keep unverified changes out of merge approval.
7. Update the board with the report, current blockers, and next owner. Summarize
   the current health, the trend, and any maintainer decision in plain English.

Do not merge PRs or push directly to the default branch. Never approve your own
implementation as the independent reviewer. Use the exact owner review-record
procedure only after an independent review passes for the current SHA.
