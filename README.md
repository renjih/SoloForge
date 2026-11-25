# Soloforge

Soloforge provides reusable GitHub Actions workflows and project rules for teams that use AI to write and independently review code.

## Set up project CI

1. Copy the [CI template](templates/ci.yml) into your project's GitHub Actions workflow directory. Set the workflow repository and ref in both `uses:` lines, then set `stack` and `check` for your project. Supported stacks are Node.js, Python, and Godot.
2. Require the [gate](.github/workflows/gate.yml) check on your default branch. For automated merges, configure `OPS_TOKEN` for the [merge bot](.github/workflows/merge-bot.yml), create the [labels](scripts/labels.sh) with `--owner YOUR_ORG --repo example-game`, and follow the [review record format](rules/REFERENCE.md#review-record-and-architecture-review).

## Project guides

- [AI project rules](rules/AI-PROJECT-RULES.md) and [implementer instructions](AGENTS.md)
- [Project setup and review reference](rules/REFERENCE.md)
- [Model selection and monthly review](rules/MODELS.md) with an [example configuration](templates/models.example.json)
- [Contributing](CONTRIBUTING.md), [security reporting](SECURITY.md), and the [MIT license](LICENSE)
