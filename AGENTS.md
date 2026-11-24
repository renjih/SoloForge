# Instructions for implementers

## Before editing

1. Read the assigned task, the relevant design and architecture documents, and the [AI project rules](rules/AI-PROJECT-RULES.md). Work on one task at a time and honor decisions already recorded by the maintainer.
2. If the repository has a `docs/mistakes` directory, use `grep` to search it for the paths you expect to change. Read the matching entries only.
3. Confirm the expected behavior, interfaces, files, deletion list, and acceptance commands. Raise ambiguities that change user-facing behavior or require approval. Resolve reversible implementation and testing choices within the task.

## While working

- Reuse existing code, preserve the specified interfaces, and follow the project's dependency direction. Make missing required data and violated invariants fail with a clear error. Handle optional inputs and supported platform differences as specified.
- For behavior changes, reproduce the failure with a focused test before implementing the fix. Preserve existing assertions and execute the full required check suite before handing off.
- Remove the superseded code, configuration, and tests listed in the task. Changes to design or policy, dependencies, checks, and CI require explicit task authorization.
- Report main-branch failures, regressions in behavior snapshots, or risks that require the maintainer to decide. Record the current blocker and what you tried if you cannot proceed.

## Before handing off

- Run the acceptance commands and [repository checks](scripts/check.sh). Report what changed, the actual command results, deviations, and any open questions.
- Leave the next step with a named role and enough context to resume. When a dispatcher owns publication, leave the changes in the working tree for it to commit, push, and open or update the pull request.
