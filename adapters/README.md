# Dispatcher adapters

An adapter is an executable `adapters/<name>/run`. The dispatcher validates the
comment, rejects fork pull requests, prepares the worktree, and publishes changes.
The adapter only implements the task and writes its summary.

| Environment variable | Meaning |
| --- | --- |
| `SF_INSTRUCTION_FILE` | Absolute path to the UTF-8 instruction file. |
| `SF_WORKDIR` | Absolute path to the prepared project worktree. |
| `SF_MODEL` | Provider model ID; an empty value selects the CLI default. |
| `SF_EFFORT` | Provider reasoning effort; an empty value selects the CLI default. |
| `SF_SUMMARY_FILE` | Private UTF-8 file to write with the final task summary. |
| `SF_LOG_FILE` | Private file to write with provider execution diagnostics. |
| `SF_METRICS_FILE` | Dispatcher-supplied private JSON file for observed `turns` and `tokens`. |
| `SF_MAX_TURNS` | Positive execution-step limit; defaults to `21`. |
| `SF_MAX_TOKENS` | Positive reported-token threshold; defaults to `244000`. |
| `SF_PAUSE_REASON_FILE` | Optional dispatcher-supplied file; write `quota` or `budget` before returning `75`. |
| `SF_TIMEOUT_MINUTES` | Positive provider deadline in minutes; defaults to `30`. |

Return `0` for success, `75` when quota or usage limits require a pause, and any
other nonzero code for failure. A paused task leaves no published commit. Treat
instruction text as data: pass it through stdin or a file, never construct a shell
command from it. Do not commit, push, or create pull requests from an adapter.

## Execution budgets

The included adapters make one provider invocation and never retry automatically.
Reaching an observed step/token limit or deadline terminates the provider process
tree and returns `75`; the dispatcher pauses without publishing partial work.
The dispatcher also enforces its own elapsed-time deadline.

Defaults were calibrated with one real Codex run on 2026-11-25: changing one file
and passing `python check.py` took 7 completed items, 19.3 seconds, and 81,247
reported tokens (80,938 input, including 74,240 cached, plus 309 output). The
defaults are three times the item count and approximately three times that token
total: 21 items and 244,000 tokens. See the
[measurement method and limitations](../docs/dispatcher.md#cost-controls) and
[sanitized record](../docs/benchmarks/codex-small-task.json). Claude was not
benchmarked because no API key was supplied; these shared limits are provisional
for Claude and remain configurable.

The included adapters classify `75` pauses as `quota` for provider usage limits
or `budget` for local time/item/token limits and native turn/budget caps. The
dispatcher uses this reason in its pause comments. Third-party adapters can omit
the reason file, in which case the comment states that the reason is unclassified.

Claude receives the supported native `--max-turns` option. Its streaming assistant
messages are counted, and a native `error_max_turns` result is a pause. Codex exec
has no native round-limit flag: its `turns` metric counts unique `item.completed`
events, including messages, reasoning, commands, file changes, tool calls, searches,
and plan updates. It stops when that completed-item count reaches `SF_MAX_TURNS`.
These counts have different provider meanings; they are bounded work measures,
not interchangeable conversation rounds. A task that reaches the cap pauses even
if its last permitted item might have been its final response.

Metrics are written atomically after events as `{"turns": 2, "tokens": null}`.
Token counts include reported input and output, including cached input once.
Codex reports usage at turn completion; Claude reports message/result usage.
`SF_MAX_TOKENS` is enforced when usage is emitted and can be exceeded before the
next usage event. It is **not a hard API billing or generation-token ceiling**.
Unavailable usage remains `null`; a paused run may therefore have unknown token
usage. Step and elapsed-time limits still apply. Echo reports one step and zero
tokens on success, or zero steps and zero tokens when simulating a pause.

The optional executable `adapters/<name>/quota` prints JSON. The Codex adapter
provides `five_hour` and `weekly` windows containing `used_percent` and `resets_at`
(Unix seconds), or `null` when unavailable. `reason` is a fixed diagnostic code.
Other adapters may omit this command. Do not expose raw quota responses, account
identifiers, auth files, or provider logs in public comments.

## Included adapters

- **Codex:** requires the `codex` CLI. The dispatcher supplies either a private
  `CODEX_HOME` containing the caller's `CODEX_AUTH_JSON`, or `OPENAI_API_KEY`.
  The adapter maps the API key to the CLI's `CODEX_API_KEY`. It writes the final
  message to `SF_SUMMARY_FILE` and recognizes quota failures from error events.
  The dispatcher owns auth installation, cleanup, and refreshed-auth writeback to
  the caller repository's secret. That writeback needs a caller-scoped token with
  repository secret write permission; the usual workflow token cannot provide it.
- **Claude:** requires the `claude` CLI and `ANTHROPIC_API_KEY`. It uses print mode
  with streamed JSON output, checks `is_error`, and extracts only the final result as the
  summary. Set a supported model and effort in your model configuration.
- **Echo:** requires only Python. It writes the instruction to
  `.soloforge/echo.txt` in the prepared worktree and writes a test summary.
  `SF_ECHO_EXIT=75` simulates a pause without changing the worktree.

Provider CLIs run with permission prompts disabled on the already authorized
worktree. Use isolated runners and only dispatch trusted same-repository work.
GitHub publication tokens, Actions runtime credentials, composite Action inputs,
Git credential overrides, the raw auth JSON, and other providers' credentials are
removed from provider process environments. Keep provider logs private; the
summary is intended for publication.

Multiple repositories sharing the same `auth.json` are **not automatically
serialized**. Per-repository workflow concurrency does not coordinate refreshes
across repositories. Prefer API keys for that setup, or arrange your own shared
queue and credential refresh management.

## Add an adapter

Implement the executable contract above, document credentials and CLI
prerequisites, and add tests that exercise success, failure, and quota pause without
real credentials. Keep helpers and fixtures in this directory. Run
`python3 -m unittest discover -s adapters -p 'test_*.py' -v` and the repository's
[full checks](../scripts/check.sh).

The dispatcher exposes `pre_task` and `post_task` hooks for integrations such as
the task board. Hooks are workflow configuration, separate from the adapter.

CLI references: [Codex automation](https://learn.chatgpt.com/docs/non-interactive-mode)
and [Claude CLI](https://code.claude.com/docs/en/cli-reference).
