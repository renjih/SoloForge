# Dispatcher setup

The [dispatch Action](../actions/dispatch/action.yml) accepts a newly created
issue or pull request comment, runs one [adapter](../adapters/README.md), then
publishes its changes. It supports GitHub.com repositories. The Action source
supplies the adapter; it does not load adapter executables from the task branch.

## Install the workflow

1. Copy the [dispatch workflow](../templates/dispatch.yml) into the caller
   repository's GitHub Actions workflow directory. Pin the Action ref to a
   reviewed commit or release. The template prepares Python, Node.js, and the two
   provider CLIs; pin CLI versions to versions you have reviewed for your setup.
2. Configure the secrets for the adapter you intend to use. Set `OPENAI_API_KEY`
   for Codex or `ANTHROPIC_API_KEY` for Claude. Codex also supports
   `CODEX_AUTH_JSON` with the credential refresh setup below. Keys belong in the
   calling step's `env:`; composite Actions do not have a `secrets` context.
3. Give the workflow Contents, Issues, and Pull requests write permissions.
   `github-token` receives `SOLOFORGE_TOKEN` when supplied, otherwise the workflow
   token. Enable the repository setting allowing Actions to create pull requests
   when using the workflow token. Use a repository-scoped token when the new PR
   must run CI without manual approval; workflow-token PR events can require
   approval. See GitHub's [workflow-token event restrictions](https://docs.github.com/en/actions/concepts/security/github_token).
4. Keep the job on `ubuntu-latest`. Dispatch only trusted same-repository work:
   the provider CLI can execute project commands with permission prompts disabled.
   PR mode refuses a fork before checkout, hooks, or adapter execution.

Workflow concurrency belongs in the calling workflow. The template serializes
comments for the same repository and issue without cancelling an active task.
Different issues can run independently. Keep the 120-minute job timeout to allow
the configured adapter timeout and credential cleanup to finish.

`repository` defaults to the calling `github.repository`; it must match the event
repository. `workdir` defaults to a fresh `soloforge-work` directory under
`runner.temp`. A pre-existing work directory is refused rather than overwritten.
Pass GitHub credentials only through `github-token` and `auth-secret-token`.

## Triggers and authorization

By default, only comments with `author_association` equal to `OWNER` are accepted.
The template filters the job and the dispatcher checks the association again.
To permit another association, change both the job condition and the
`allowed-associations` JSON input. Treat every added association as permission to
run code with the configured provider credentials.

`triggers` is a JSON object, defaulting to
`{"/codex":"codex","/claude":"claude"}`. The configured slash command must start
the comment and be followed by whitespace and a nonempty instruction. For example:

```text
/codex Fix the failing parser test and run the project checks.
Tier: normal
```

Issue mode creates a branch named `soloforge/issue-<number>-comment-<id>` from the
default branch and opens a PR after changes are committed and pushed. Replaying
the same comment when that branch exists returns `duplicate`; continue by
commenting on its PR. A different comment creates a distinct task branch.

PR mode checks out the current head of a same-repository open PR and pushes to
that branch. It refuses the default branch and a head that changed before
checkout. Neither mode merges the PR. Exit code `75` from an adapter or a quota
pause posts a pause comment and leaves no task changes committed or pushed.

## Cost controls

API key usage is billed by the provider. Set a monthly spending cap in your AI
provider dashboard before enabling dispatch, and monitor actual billed usage.
The Action provides configurable per-task limits:

| Action input | Default | Enforcement |
| --- | --- | --- |
| `timeout-minutes` | `30` | Stops the adapter process and its descendants after the configured execution time. |
| `max-turns` | `21` | Limits observed turns or completed work items; Claude also receives its native turn limit. |
| `max-tokens` | `244000` | Stops when reported input plus output token usage reaches the configured budget. |

On 2026-11-25, one real `codex exec --json` invocation changed `value.py` from
`VALUE = 1` to `VALUE = 2` and ran `python check.py` successfully. Codex CLI
`0.171.0-alpha.13.2`, medium effort, and the provider CLI default model were used
on Windows PowerShell, with user configuration and rules disabled. The JSON
stream did not emit a model ID. The run completed **7 items** in **19.3 seconds**
and reported **80,938 input tokens**, including **74,240 cached input tokens**,
plus **309 output tokens**: **81,247 total reported tokens**. The 21-item default
is exactly three times the observation; 244,000 tokens rounds three times the
reported total (243,741) up to the next thousand. Cached input is already part of
the input total and is not added again. See the [sanitized benchmark record](benchmarks/codex-small-task.json).

This is one small-task calibration, not a cost guarantee across models, projects,
CLI versions, or operating systems. Claude was **not benchmarked** because no
`ANTHROPIC_API_KEY` was supplied; the shared defaults are provisional for Claude.

These inputs become `SF_TIMEOUT_MINUTES`, `SF_MAX_TURNS`, and `SF_MAX_TOKENS` in
the adapter environment. Claude exposes conversation turns; Codex supervision
counts completed output and tool work items because its execution interface does
not provide the same native turn control. The token supervisor can stop only
when the provider emits usage. An in-flight request can exceed the budget, and a
CLI that omits usage cannot provide a measured token ceiling. These controls do
not replace a provider spending cap.

The dispatcher permits at most three starts per issue or PR in a rolling
60-minute window. It refuses a fourth attempt and comments without starting the
provider. Serializing the same issue in the workflow keeps this limit effective.
Failures never trigger an automatic retry; a new authorized comment is required
to start another attempt and still counts against the hourly limit.

At the end of an authorized task attempt, the dispatcher posts its outcome,
observed rounds, and elapsed time, including failure, quota pause, and timeout.
Execution-time or usage exhaustion stops the task without publishing its changes.
Missing provider usage is reported as unavailable rather than as zero.
Pause comments distinguish insufficient provider quota from an exhausted execution
budget. A third-party adapter returning `75` without a reason is reported as an
unclassified adapter pause rather than being mislabeled as quota exhaustion.

Reservations count only when posted by an account with current repository write,
maintain, or admin permission, or by the authenticated bot itself. Bot identity
comes from GitHub's response to the new reservation, and account permissions come
from GitHub's collaborator-permission API. An outsider copying a reservation
marker cannot consume the hourly allowance. Rejected starts retire their own
reservation marker so they do not extend the cooldown.

## Runtime model and quota configuration

The [default runtime configuration](../actions/dispatch/defaults/models.json)
defines adapter routes, quota thresholds, and hooks. An optional
`.soloforge/models.json` from the caller repository's fetched default-branch
commit recursively overlays those defaults. The entire configuration, including
hooks and model routes, is read from that immutable commit even in PR mode;
configuration proposed on the PR branch is not executed. A partial override can
change one route without copying every tier:

```json
{
  "adapters": {
    "codex": {
      "tiers": {
        "normal": {"model": "approved-model-id", "effort": "medium"}
      }
    }
  }
}
```

Supported tiers are `mechanical`, `normal`, `heavy`, `ultra`, and `rework`. An exact
`Tier: <tier>` line selects a tier; otherwise issue tasks use `normal` and PR
tasks use `rework`. Every route needs string `model` and `effort` values. Default
model strings are empty so the provider CLI chooses its own default model; set
explicit approved model IDs for reproducible selection. Default efforts are
`low`, `medium`, `high`, `high`, and `high` in tier order. Codex and Claude also
have a `downgrade` route with an empty model and `medium` effort.

Quota thresholds are percentages of **remaining** quota. The defaults pause at
5% remaining in the five-hour window or the weekly window, pause new issue tasks
at 15% or less weekly remaining, and downgrade `heavy` or `ultra` tasks at 30%
or less weekly remaining. The JSON keys are `five_hour_pause_remaining`,
`weekly_stop_remaining`, `weekly_rework_only_remaining`, and
`weekly_downgrade_remaining` under `quota`. Weekly thresholds must be ordered
from lowest to highest. A provider without usable quota data proceeds; an
adapter can still return `75` when its provider reports a usage limit.

The [model-pair example](../templates/models.example.json) records approved writer
and reviewer choices and monthly reviews. It has a different purpose from this
runtime routing configuration; follow the [model policy](../rules/MODELS.md).

## Trusted task hooks

Set `pre_task` and `post_task` Action inputs, or set `hooks.pre_task` and
`hooks.post_task` in the runtime configuration. A nonempty Action input takes
precedence. These are trusted Bash commands executed in the prepared task
checkout. Never build them from comment text or other event fields. Review hook
changes before dispatching code with credentials.

Hooks receive the adapter's `SF_` contract variables plus `SF_REPOSITORY`,
`SF_OWNER`, `SF_ISSUE`, `SF_TASK_ID`, `SF_MODE`, `SF_BRANCH`, `SF_ACTION_ROOT`, and
`SF_TIER`. They receive `GH_TOKEN` for caller-repository integration, but no
provider keys or raw auth JSON. Read instructions from `SF_INSTRUCTION_FILE` as
data. The pre-hook sees `SF_RESULT=running`; the post-hook receives `SF_RESULT`
and `SF_PR_URL` after the task attempt. Post-hook results include `done`,
`no_change`, `paused`, or `failed`. Failures before the task attempt may occur
before hooks start. A failed hook fails the Action; it cannot undo an already
published commit.

Successful Action outputs are `result`, `branch`, and `pr_url`. Results also
include `ignored` for unmatched or unauthorized comments, `refused` for fork
PRs, and `duplicate` for an existing issue task branch. An adapter or command
failure fails the Action. Provider diagnostic logs stay private; adapters should
write a concise publishable summary without credentials or personal data.

## Codex login refresh

When using `CODEX_AUTH_JSON`, also set `SOLOFORGE_AUTH_TOKEN` and pass it as
`auth-secret-token`. This token needs Actions repository secrets write permission
on the **caller repository**. The dispatcher installs the login in a private
temporary Codex home and writes refreshed credentials back to that repository's
`CODEX_AUTH_JSON` secret. It refuses login-based execution without a writeback
token. When `OPENAI_API_KEY` is present, the API key takes precedence.

Normal completion, provider failure, and a handled timeout allow credential
cleanup and refresh writeback. Forced job cancellation, runner loss, or a hard
job timeout can prevent cleanup and lose refreshed credentials. Affected callers
may need to log in again and replace their secret. Prefer API keys for automation
that cannot tolerate this limitation.

Multiple repositories sharing one `auth.json` are not automatically queued
together. Their independent refreshes can invalidate a shared login. Use API
keys, or provide your own cross-repository queue and credential management.
