# AI project rules

Read this before assigning, implementing, or reviewing work. Use the [reference](REFERENCE.md) for procedures and templates and the [model policy](MODELS.md) to configure independent writing and review.

## 1. Roles and handoff

| Role | Responsibility |
| --- | --- |
| Maintainer | Sets product direction, decides changes that need approval, and evaluates milestones. |
| Architect and reviewer | Maintains the design and codebase health, writes task cards, and independently reviews the work. |
| Implementer | Makes the changes and runs the checks for one assigned task at a time. |
| Merge automation | Enforces the configured checks and approvals for eligible pull requests. |

1. Put the complete task in the dispatch message or point to an accessible, checked-in task. Include the intended branch and a title that states the outcome.
2. The implementer delivers the patch and the actual check results. When a dispatcher owns publication, it commits, pushes, and opens or updates the pull request.
3. The reviewer runs the checks, records findings against the current head commit, and sends requested implementation changes back to the implementer. Keep the providers for implementation and review independent.
4. Give eligible milestones `P2` and routine changes `P3` after review. Configure merge automation to require checks and approval for the reviewed commit. The maintainer handles fork pull requests.

## 2. Project setup

Before assigning feature work, establish the project's architecture, invariants, checks, and behavioral baselines. See [project setup](REFERENCE.md#2-project-setup).

## 3. Implementation

- Read the task, the [implementer instructions](../AGENTS.md), and the relevant design and architecture. Use the task's exact interfaces and stay within its scope.
- Keep dependencies flowing in the documented direction. Reuse existing data, functions, and modules when they express the required behavior.
- Fail visibly when required configuration, data, or state is missing. Add fallbacks for specified optional data or supported platform differences and explain the reason where it matters.
- Remove the fields, functions, configuration, and tests on the task's deletion list. Keep tests that still describe supported behavior.
- For behavior changes, write a focused failing test, verify it fails for the expected reason, implement the change, and run the full checks. Preserve test assertions unless the task explicitly changes the contract.
- Change policy, architecture, dependencies, CI, or check scripts only when the task authorizes it. Fix the cause of a check failure.

## 4. Task design and review

Write the intended outcome, exact interfaces, files, deletion list, concrete test inputs and expected outputs, and acceptance commands. Split changes that are too large to review independently. Use the [task card template](REFERENCE.md#appendix-d-task-card-template).

For a new system or module, a design or data format change, or a recurring bug, confirm the desired outcome against existing decisions before dispatching. Identify up to three conditions that could invalidate the proposed approach. Record how the plan handles each one and revise the card if any condition invalidates it.

Complete these steps before approving a change.

1. Compare the interfaces, dependency direction, invariants, and deletion list against the task. Check that the diff stays within scope.
2. Read assertions for concrete outcomes and run the required checks. Compare health metrics with the base branch and explain regressions.
3. Look for silent fallbacks, swallowed errors, unnecessary dependencies, and retained code that the change replaces.
4. Identify up to three likely failures in real use. Exercise each with a test or a reproducible run and record the result.
5. Record what ran and the reviewed commit. Recheck subsequent changes before restoring approval. Escalate unresolved review findings after three rework rounds.

The [review procedure](REFERENCE.md#review-record-and-architecture-review) describes the merge automation's review record and architecture labels.

## 5. When to stop and escalate

Pause the affected work and contact the maintainer when the default branch is red, a behavior snapshot or reproduction test fails on the baseline, a metric worsens in two consecutive health reviews, design debt exceeds ten items, or a task would change more than three architecture layers. Escalate spending, permissions, credentials, licensing concerns, and public releases before acting. See [project health checks](REFERENCE.md#5-project-health-checks).

## 6. Cleanup

Schedule cleanup as reviewable work and demonstrate preserved behavior with fixed inputs. See the [cleanup procedure](REFERENCE.md#6-cleanup).

## 7. Design debt

Track deferred replacements and delete superseded code when the replacement lands. See the [design debt procedure](REFERENCE.md#7-design-debt).

## 8. Reporting

State the result, evidence, risks, and any next action in plain English. Put requests for the maintainer first. See [reporting](REFERENCE.md#8-reporting).

## 9. Priorities and decisions

| Priority | Meaning | Action |
| --- | --- | --- |
| `P0` | Work must pause | Notify the maintainer immediately. |
| `P1` | A maintainer decision or evaluation is needed | Explain the decision, options, trade-offs, and next action. |
| `P2` | Milestone, cleanup, or health review | Complete review and record the outcome. |
| `P3` | Routine change or review follow-up | Complete review and include it in the normal handoff. |

- Decide reversible implementation, architecture, sequencing, and verification details within the approved scope. Record the reason when it affects later work.
- Ask about changes to user-facing behavior or product direction. An explicitly authorized default may take effect after 48 hours when the recommendation is reversible and its consequences are clear.
- Wait for explicit approval for platform or spending commitments, permissions or credentials, removing unlisted content or shipped features, reversing maintainer decisions, changing models, weakening project rules, or irreversible changes. If a decision requires the maintainer to use or inspect something, specify what to try and what response you need.
- Present one decision at a time with brief context, the recommended option, and the consequences of each option. Record the maintainer's decision before resuming the affected work.
