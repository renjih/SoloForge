# WeChat mini-game starter

Copy this directory's contents, including `.github/`, to a new repository such as
`example-game`. The starter provides a small canvas scene, testable pure logic,
package checks, a SoloForge gate, and the comment dispatcher.

## Setup

1. Use Node.js 22 or later, run `npm ci`, then `npm run check`.
2. Import the folder into WeChat Developer Tools as a mini-game project. The
   [project configuration](project.config.json) uses the public `touristappid`
   placeholder. Keep upload credentials and private local settings out of Git.
3. Review and pin the SoloForge refs in the [CI workflow](.github/workflows/ci.yml)
   and [dispatch workflow](.github/workflows/dispatch.yml). Require `gate / gate`
   on the default branch. The gate runs on label events as well as code events.
4. For dispatch, configure `OPENAI_API_KEY` for Codex or `ANTHROPIC_API_KEY` for
   Claude. The optional `SOLOFORGE_TOKEN` needs Contents, Issues, and Pull requests
   write access to this repository. Without it, the workflow token is used and
   automated PR checks can require manual approval. Codex login-based use also
   needs `CODEX_AUTH_JSON` and `SOLOFORGE_AUTH_TOKEN` with repository secrets write
   access so refreshed login credentials can be saved to this repository.

API key usage is billed by the provider. Set a monthly spending cap in its
dashboard before enabling dispatch. The workflow defaults to 30 minutes,
21 observed turns or work items, and 244,000 reported tokens per task. Token checks
depend on emitted usage and can overshoot during an in-flight request. The
dispatcher allows three starts per issue or PR per hour and never automatically
retries failed tasks.

The dispatcher accepts owner comments beginning with `/codex` or `/claude` and a
nonempty instruction. It refuses fork PRs, runs on a hosted runner, and writes
handoffs through the trusted pre/post board hooks to the orphan `board` branch.
API keys are recommended when several repositories would share one Codex login;
those repositories do not share a dispatch queue.

## Optional automatic merging

Automatic merging is disabled until you set repository variable
`SOLOFORGE_AUTO_MERGE` to `true` and configure `SOLOFORGE_TOKEN`. Create the
priority and blocker labels for your project. Eligible same-repository PRs need
a successful current-head gate, `P2` or `P3`, no blocker labels, and an owner
comment whose first line is `## Review record` and whose single
`Reviewed commit: <SHA>` line names the exact 40-character current head SHA.
Publish that record only after independent review passes. Fork PRs require the
owner to merge them manually.

## Checks and code boundaries

| Check | Behavior |
| --- | --- |
| Configuration | Valid JSON, game compile type, AppID placeholder, and supported orientation. |
| Syntax | All JavaScript and module files pass Node.js syntax checks. |
| Logic | Modules under `js/core*` and `js/logic/` do not call platform or canvas APIs. |
| Package budgets | Main package at most 4MB; main plus declared subpackages at most 30MB. Ignored files are excluded; only `folder` and `file` ignores are supported. |
| Tests | Twelve retained regression cases run with the built-in Node.js test runner. |

The [checker](scripts/check.mjs) reads subpackages from `game.json`, including the
legacy `subPackages` spelling. Keep rendering and platform calls in `game.js` or
render modules; put rules, scoring, and level logic into pure functions with
tests. Manual device preview and publishing happen in WeChat Developer Tools.
