# Contributing

Use an [issue template](.github/ISSUE_TEMPLATE/) to report a bug, propose an improvement, or suggest an adapter or project template. Use the private reporting channel for [security issues](SECURITY.md).

## Prepare a change

1. Read the [AI project rules](rules/AI-PROJECT-RULES.md) and [implementer instructions](AGENTS.md). Keep each pull request focused on one outcome.
2. Include concrete acceptance criteria and verification steps. For adapters and templates, describe the interfaces, required inputs, and how a new user can verify the setup.
3. Use fictional project names such as `example-game` and `demo-app`. Keep secrets and personal data out of source, examples, logs, and attachments. Supply repository and owner values through workflow inputs or required CLI arguments or environment variables.

## Change a public workflow

Pass event fields and trigger words through step environment variables, then quote shell expansions when reading them. Avoid inserting comment text, titles, or branch names directly into executable workflow scripts. Preserve the fork boundary: compare the complete head and base repository names, run fork code on `ubuntu-latest`, and keep credentials away from fork code. See the [security policy](SECURITY.md#workflow-trust-boundaries).

Add a regression case when changing these boundaries. Workflow and gate tests must exercise observable behavior, including rejection of an ineligible merge or an outdated review record.

## Run checks

Install Bash, Python 3.11, and Node.js 22. From the repository root, install the test dependency and run the checks used by CI.

```bash
python3 -m pip install PyYAML==6.0.2
bash scripts/check.sh
```

The [check script](scripts/check.sh) validates shell syntax, JSON, local Markdown link targets, and common disclosure patterns, then runs the architecture and workflow tests.

## Open a pull request

Describe the user-visible outcome, list the checks and their results, and note any removed tests and the features they covered. The maintainer handles merging pull requests from forks. For the automated approval record and architecture labels, see the [review procedure](rules/REFERENCE.md#review-record-and-architecture-review).
