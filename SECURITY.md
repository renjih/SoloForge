# Security policy

Report suspected vulnerabilities through this repository's private vulnerability reporting channel on GitHub. If private reporting is unavailable, contact a maintainer privately to arrange a reporting channel. Keep vulnerability details out of public issues, pull requests, and comments while the report is being handled.

Include the affected workflow, script, or template and its ref, the conditions needed to reproduce the problem, the observed impact, and any safe workaround. Redact credentials, personal data, and other sensitive values from logs and attachments.

Maintainers will use the private report to coordinate validation, a fix, and disclosure. The default branch is the starting point for fixes. Include any other affected refs in your report so maintainers can assess their impact.

## Workflow trust boundaries

- Pull requests from forks run the gate on `ubuntu-latest`, even when a repository configures a self-hosted runner. Do not pass repository secrets to fork code or run it through `pull_request_target`.
- Merge automation refuses fork pull requests. Its scheduled run adds `needs-owner` so a maintainer can review and merge them manually.
- Automatic merging requires a successful gate for the current head commit, an eligible priority label, no blocking labels, and an exact owner review record for that commit. A new commit invalidates the previous record. See the [review procedure](rules/REFERENCE.md#review-record-and-architecture-review).
- Treat comment bodies, titles, branch names, and configured trigger words as untrusted data. Pass them through step environment variables and quote shell expansions. Never interpolate those values into a workflow `run:` block.
- Give tokens only the permissions the selected workflow needs. Keep credentials in repository secrets and review changes to workflows, adapters, and setup commands before allowing them to run with those credentials.
