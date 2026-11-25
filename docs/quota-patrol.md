# Quota patrol

[The patrol script](../scripts/quota_patrol.py) reads a quota ledger in a repository
you name. It calculates usage and proposes notification text; it does not send
messages, email, retry tasks, or invoke an AI provider.

## Configure the ledger

Create an issue in the caller repository and set `quota.ledger_issue` in that
repository's `.soloforge/models.json` to its positive issue number. When configured,
the [dispatcher](../actions/dispatch/dispatch.py) appends sanitized JSON records to
that issue. The ledger always belongs to the caller repository. There is no
central ledger, built-in owner, or default issue number.

Records contain task identity, adapter/model/tier, result, duration, observed
turns/tokens, quota level, and available before/after quota windows. They exclude
instructions, summaries, provider logs, credentials and raw quota/account data.
Unavailable measurements are `null`. API-key runs may have token metrics without
account quota windows. Usage is what the CLI reports, not an independent billing
measurement; see [execution-budget limits](../adapters/README.md#execution-budgets).

Run a report with explicit repository and ledger configuration:

```bash
python3 scripts/quota_patrol.py report \
  --owner example-owner --repo example-game --ledger-issue 42 \
  --out patrol.json --pushed-out pushed.txt
```

`SOLOFORGE_OWNER` and `SOLOFORGE_LEDGER_ISSUE` can replace those two flags. `--repo`
is required and names the repository without its owner. The default display/day
boundary timezone is UTC; use `--timezone` with an installed IANA timezone if
needed. Optional `--triggers` accepts a JSON list or an object with a `data` list.
Each task may contain `id`, `name`, `model`, `status`, `fired_at`, `finished_at` and
`enabled`, or equivalent `last_run`/`derived_state` fields. The patrol's own task
name defaults to `Quota patrol` and can be changed with `--self-name`.

The newest Codex dispatcher record with an explicit quota level supplies current status
when newer than the issue's optional `status` section. Weekly usage deltas are
counted only when both snapshots use the same reset window and usage increases.
Unknown windows and resets are reported as missing measurements. Integer token
metrics are reported totals, including cached input. Legacy dictionary metrics
with `input`, `cached_input` and `output` preserve separate cache-excluded totals,
which the summary labels explicitly alongside reported totals. Claude/Echo records
without account quota cannot replace the latest Codex quota snapshot.

## Notification state

`patrol.json` contains `stats`, `push_message`, `daily_message`, `alerts`, errors and
proposed `pushed_text`. New low/rework-only/stopped levels trigger once; recovery
updates state silently. Failed/stuck tasks are deduplicated by task and run time.
Missing/stale quota reminders occur at most weekly. Actions usage alerts stay
acknowledged until an explicit normal status appears. No proposed alert category
is dropped when several fire together.

After the operator's notification integration successfully delivers a proposal,
update only the issue's notification state:

```bash
python3 scripts/quota_patrol.py mark-pushed \
  --owner example-owner --repo example-game --ledger-issue 42 --pushed pushed.txt
```

This replaces only the `<!-- pushed:start -->` / `<!-- pushed:end -->` section,
preserving any `status` section. Duplicate, reversed, missing or overlapping markers fail.
An empty state file leaves the issue unchanged. Do not mark a notification as
delivered before delivery succeeds. Coordinate concurrent body writers through
your workflow's concurrency group to avoid overwriting each other's state.

For offline verification, `report` accepts `--issue-file`, `--comments-file`, and
`--now`. The [tests](../scripts/test_quota_patrol.py) use only fixtures and mocked
GitHub calls. [Runner installer tests](../scripts/test_runner_setup.py) likewise
fake downloads and registration; they do not register a runner.
