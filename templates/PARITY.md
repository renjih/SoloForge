# Player-visible parity checklist

Use this checklist to compare the main branch with a reference the maintainer
has approved. A feature matches only when a player can see and use the same
behavior on the main branch. Implemented rules without the visible experience
remain `in_progress`.

- Project: `example-game`
- Reference branch or artifact: `<reference>`
- Approved version: `<version>` at commit `<SHA>`, approved on `<YYYY-MM-DD>`
- Decision record: `<location of the maintainer's approval>`
- Main-branch parity: `<matched>/<total excluding not_pursued>`
- Ready for a first playtest: `<yes or no; outstanding required rows>`
- Last verified: `<YYYY-MM-DD>` at main commit `<SHA>`

Use only `matched`, `in_progress`, `not_started`, and `not_pursued`.
`not_pursued` needs an explicit maintainer decision and a pointer to its record.
Every row marked as required for a playtest must be `matched` before reporting
the main branch ready for that playtest.

| Item | Behavior a player can see or use | Reference version | Main task or PR | Status | Required for playtest | Evidence or decision |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Start and finish one playable run | `<version>` | `<task or PR>` | `not_started` | yes | `<verification steps>` |

Verify against a runnable main-branch build and record the result. Keep this
checklist with the project's reviewed documentation; it does not authorize
publishing, merging, or changing the approved reference.
