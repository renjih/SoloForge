# AI project reference

The [AI project rules](AI-PROJECT-RULES.md) define the baseline. Use these procedures when setting up a project, reviewing changes, or reporting progress.

## 2. Project setup

The architect completes project setup before feature implementation starts.

1. Record three to five project invariants. Describe the layers, module responsibilities, and allowed dependency direction using the [architecture outline](#appendix-a-architecture-outline).
2. Implement health checks. For each blocking rule, verify that a deliberately invalid case fails the check. Record a baseline and keep a metrics history for later comparisons.
3. Configure the [CI template](../templates/ci.yml) to run the full project checks on pull requests. Require a successful gate before merging.
4. Capture behavior snapshots for stable outputs using fixed inputs and seeds. Protect existing behavior during later cleanup by comparing the complete outputs.
5. Schedule a weekly health review as a fallback. Record the check command, where metrics are stored, and who owns follow-up.

## Review record and architecture review

The repository owner records the independent review in a PR comment. The [merge workflow](../.github/workflows/merge-bot.yml) reads the heading and reviewed commit.

```text
## Review record
Reviewed commit: <full 40-character head SHA>
Review model: <provider and model used>
Checks: <commands and results>
Findings: <rework addressed and remaining risks>
```

Keep the heading as the first line and include exactly one `Reviewed commit: <SHA>` line with the complete 40-character current head SHA. The comment author must be the repository owner; a model name or a bot login does not establish approval. Every new head commit needs a matching owner record.

Publishing this record authorizes merge automation to treat the commit as reviewed. Publish it only after the independent review passes and requested changes are resolved. The automation validates the author and commit format, so it cannot interpret a negative verdict hidden in the findings. Use ordinary comments for failed reviews, remove `P2` or `P3` while changes await review, and keep the appropriate blocking label until resolved. Restore the eligible priority label after approval. Fix conflicts or stale branches and verify the new head before approving it.

The record alone does not merge a PR: a successful gate for the same head, eligible priority labels, and the absence of blocking labels are also required. Fork pull requests always require the maintainer to merge them manually.

For requested changes, include the file and location, why the behavior is wrong, the expected result, and the command that verifies it. Link the latest handoff and give the implementer the previous reviewed commit when asking for a follow-up. Document recurring defects in the project's mistake log with the symptom, root cause, fix, and prevention.

Review new modules, cross-module interfaces, persisted data or config schemas, shared CI, new dependencies, and changes spanning several modules against the project's architecture. The gate reads the check types from [architecture rules](../docs/arch-rules.json).

| Type | Fields |
| --- | --- |
| `forbid` | `files` glob or glob list, `pattern` regular expression, optional `except` glob or glob list |
| `max_lines` | `files` glob or glob list, `max` line count |
| `require` | `path` that must exist |
| `manual` | Use the string `"manual"` for decisions that require human or model review. |

Use `needs-arch` while architecture review is pending. When the change satisfies the rules and any escalated findings are resolved, remove `needs-arch` and apply `arch-ok`. Use `arch-veto` to block a rejected approach. Where feasible, turn recurring architecture findings into automated rules with a regression test. See [model selection and escalation](MODELS.md#architecture-review).

## 5. Project health checks

Run the tests and automated invariants on every pull request. Run a broader health review after three merged changes, when a plan finishes, or after fourteen days with new merges, whichever comes first. Use the [weekly fallback](#appendix-c-weekly-fallback) to catch missed reviews.

1. Run project checks and record metrics against the current default-branch commit. Compare them with the last report and explain regressions.
2. Verify that module responsibilities, dependency directions, and design debt still reflect the code.
3. Ask an independent reviewer to look for duplicated systems, overlapping responsibilities, dead code, excessive guards, and silent fallbacks. Verify each finding before recording it.
4. Record the follow-up and its owner. Give necessary cleanup priority over the next feature. See [the escalation rules](AI-PROJECT-RULES.md#5-when-to-stop-and-escalate) for conditions that pause work.

When the design changes, identify affected modules and the fields, functions, or configuration the new design supersedes. Update the design debt list before assigning follow-up tasks.

## 6. Cleanup

- Assign cleanup its own task and review. Start from the current base and capture the baseline tests and outputs before editing.
- Remove dead or superseded code without changing behavior. Run the same fixed inputs before and after cleanup and compare outputs exactly.
- Preserve code whose removal would change behavior and record why. Put code slated for replacement in the design debt list and remove it with that replacement.
- List any files, branches, or other artifacts the maintainer has authorized for deletion explicitly. Obtain approval before deleting anything outside that list.

## 7. Design debt

Record the current implementation, intended design, trigger for replacement, and what to delete. Remove the debt entry when the replacement and deletion land. If there are more than ten open items, pause feature work to reduce the debt.

## 8. Reporting

Lead with a requested decision or a blocker, then state the outcome, how it was verified, and the next owner and action. Describe consequences in terms the maintainer can evaluate. State any uncertainty and the evidence needed to resolve it.

## 9. Maintainer updates

Keep outstanding decisions, evaluations, and maintainer-only actions visible in the handoff until resolved. Notify the maintainer immediately for a stop condition, a delivery blocker only they can resolve, or a milestone that is ready for them to try. Include routine progress and retries in the regular handoff.

## Appendix A. Architecture outline

- **Overview**. Describe the dependency direction and each layer in plain language.
- **Modules**. Give each module's purpose, location, and callers.
- **Rules**. Explain each invariant, its reason, and how to check it.
- **Design debt**. List the current implementation, intended replacement, trigger, and removal scope.
- **Review**. Record the last verified commit. Update the outline when a module is added, removed, or renamed, or when allowed dependencies change.

## Appendix B. Health check inputs

| Area | Inspect |
| --- | --- |
| Invariants and layers | Project rules, dependency direction, and deterministic behavior |
| Size and complexity | File and function sizes, complexity, duplication, and unused code |
| Failure handling | Missing required fields, ignored errors, and fallbacks that hide invalid state |
| Behavior | Snapshot changes, reproducible bugs, and support for existing data |

Calibrate thresholds against the baseline. Track source and test size, the largest file and most complex function, duplicate and unused code, null guards, silent fallbacks, and counts of blocking and advisory findings. Compare counts relative to source size where appropriate.

## Appendix C. Weekly fallback

Run the project's normal health commands against the current default branch. Publish the checked commit, trends, and any unresolved decisions or follow-up tasks. Revisit unusually tight coupling, duplicated systems, and architectural debt in the same review.

## Appendix D. Task card template

```markdown
## Task title

**Goal**
Describe the outcome in one sentence.

**Files**
List the files to create, change, and test.

**Interfaces and invariants**
List exact signatures, required inputs, outputs, and dependency boundaries.

**Deletion list**
List superseded code, configuration, and tests. Write None if empty.

**Tests and acceptance checks**
Give concrete inputs and expected outputs. List commands and expected results.

**Scope boundaries**
List related work that belongs to a different task.
```
