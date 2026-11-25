# Architecture audit

Inputs: an explicit repository list, review period, architecture documents, and
the approved independent reviewer. Read the [project rules](../rules/AI-PROJECT-RULES.md)
and [architecture review procedure](../rules/REFERENCE.md#review-record-and-architecture-review).

1. Check whether each repository changed during the review period. Record and
   skip unchanged repositories. Read available architecture and decision records,
   recent merged PRs, and existing architecture-debt issues.
2. Ask the designated independent reviewer to inspect dependency direction,
   excessive coupling, duplicate systems, growing files and functions, versioned
   data or config migrations, and divergence from shared workflows or templates.
   Give it concrete changed files and the existing decisions.
3. Verify each finding against the source. Report no more than five material
   findings per repository. Each needs a location, observed problem, likely
   consequence, proposed correction, size estimate, and verification command.
   Say when no material problem was found; do not add findings to fill a quota.
4. Check existing automated architecture rules before proposing another. For a
   recurring violation, suggest a `forbid`, `max_lines`, or `require` rule when
   possible; use manual review only when the condition cannot be checked reliably.
5. Update an existing issue for the same debt instead of opening a duplicate.
   Separate routine corrections from choices requiring the maintainer. Record
   owner decisions on the board with options and a recommendation; never infer
   approval from silence when explicit permission is required.
6. Summarize repositories reviewed or skipped, verified findings, and requested
   decisions. Include evidence links and the next owner for each action.

This audit does not change code, dispatch implementation, apply merge-eligibility
labels, push branches, or merge PRs. Any proposed fix goes through a separate
reviewed task.
