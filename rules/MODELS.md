# Model selection and review

Assign writing and code review to models from different providers. Choose by the provider that develops the model, even when both models are available through the same tool. The project maintainer approves the pair and any exception to provider independence.

## Choose the pair

1. Give writer candidates the same representative tasks and acceptance checks. Compare test results, adherence to the specified interfaces, rework, latency, and cost.
2. Give reviewer candidates the same diffs and task cards, including changes with known defects. Compare which defects they catch, the accuracy of their findings, and how often a finding leads to useful rework.
3. Pick a writer and a reviewer from different providers. Record the exact model IDs accepted by the tools that will run them. Copy the [example configuration](../templates/models.example.json), replace the placeholder values, and commit the approved settings with the project. Treat the example as a record of the team's choices and map those choices to your runner's inputs if automation uses a different schema.
4. Give the reviewer the task, diff, repository rules, and reproducible checks. Record the actual review provider and model with the reviewed commit. See the [review record](REFERENCE.md#review-record-and-architecture-review).

## Dispatcher runtime configuration

The [example configuration](../templates/models.example.json) records the approved
writer and reviewer pair. The dispatch Action instead reads runtime routes and
quota policy from `.soloforge/models.json` in the caller repository, overlaid on
the [runtime defaults](../actions/dispatch/defaults/models.json). Set approved
model IDs there; empty default model strings select the provider CLI's default.
See [dispatcher configuration](../docs/dispatcher.md#runtime-model-and-quota-configuration)
for tier selection, partial overrides, quota thresholds, and trusted hooks.

## Architecture review

- Use the normal reviewer to evaluate changes against the existing [architecture rules](../docs/arch-rules.json), including manual rules. Send violations of existing rules back with the specific rule and a verification step.
- Ask the designated architect to review large task plans before implementation and to resolve new architecture questions or specific uncertainties that the rules cannot settle. Record an approval, requested changes, or a rejection with the reason. Keep architecture approval pending until findings are resolved and verified.
- Keep the architect separate from the implementation when assigning a model to that role. Use recurring findings to improve the documented rules and automate checks where feasible.

## Monthly review

1. Set `next_review` when the maintainer approves the first configuration. Each month, review the current pair and check provider release notes for changes to availability, pricing, and quotas.
2. Run the same representative tasks and known-defect reviews for promising candidates. Compare results against the current pair and summarize the evidence, trade-offs, and recommendation to keep or change models. Do this even when there are no new releases.
3. Keep the approved pair until the maintainer explicitly approves a change. After each decision, record the result and date, update `last_reviewed` and `next_review` in `YYYY-MM-DD` format, and verify provider independence after any change.
