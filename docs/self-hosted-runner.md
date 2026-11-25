# Owner-only self-hosted runners

Use GitHub-hosted runners for public repositories. GitHub recommends self-hosted
runners only for private repositories because public forks can submit workflow
code that compromises the runner. A condition inside a reusable workflow cannot
prevent a pull request from adding another workflow that selects the same runner.
See [GitHub's installation warning](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
and [runner lifecycle guidance](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners).

SoloForge's gate selects a self-hosted runner only for the repository owner's own
same-repository PR. Fork PRs and other contributors use `ubuntu-latest`. This
condition helps route trusted jobs; it does not isolate a public repository's
runner. Keep `CI_RUNNER` unset in public repositories. An organization choosing
another approach must manage its own runner-group restrictions, isolated machines,
trusted workflow entrypoints, and lifecycle controls outside this installer.

## Configure a private runner

Use a dedicated disposable Linux machine or VM and a non-root account without
personal credentials, production network access, or passwordless sudo. Install
Git, Bash, Python, Node and the dependencies required by your project separately.
The installer supports Linux x64 and arm64 and needs `gh`, `curl`, `sha256sum`,
`tar`, `realpath`, and `mktemp`.

Obtain an explicit version and the matching SHA-256 from the
[official runner release](https://github.com/actions/runner/releases). Supply a
short-lived `GH_TOKEN` scoped to runner registration for the named repository.
Do not place it in command text or a configuration file. Then run the
[installer](../scripts/runner/setup.sh):

```bash
bash scripts/runner/setup.sh \
  --owner example-owner --repo example-game \
  --install-dir ./runner-example-game \
  --version "$RUNNER_VERSION" --sha256 "$RUNNER_SHA256"
```

The owner is required through `--owner` or `SOLOFORGE_OWNER`; repository, release,
digest and install directory must be explicit. The directory must not exist yet;
its parent directory must already exist.
The script checks repository visibility before downloading, verifies the archive
before extraction, and configures an `--ephemeral` one-job runner with the label
`soloforge-owner`. It refuses public repositories and root execution. It does not
install packages, change host settings, grant sudo, delete existing data, install
a service, start a runner, or set `CI_RUNNER`.

The helper fetches a one-hour repository registration token through the
[registration API](https://docs.github.com/en/rest/actions/self-hosted-runners#create-a-registration-token-for-a-repository),
or accepts an existing token through `RUNNER_REGISTRATION_TOKEN`. It passes that
token through the runner's
[supported input environment variable](https://github.com/actions/runner/blob/main/src/Runner.Listener/CommandSettings.cs),
so the token does not appear in command arguments or installer output. `GH_TOKEN`
is still needed to verify repository visibility. Registration output stays on the
isolated machine; failures produce a fixed error message.

Each installer invocation registers exactly one ephemeral runner for one job.
Remove the bootstrap token and any cached operator login, then start that already
registered runner with `run.sh` in its install directory from a clean environment.
Starting `run.sh` connects the existing registration; it does not register a new
runner.

After its job finishes, the runner deregisters. Before **every next job**, destroy
the VM or securely recreate its filesystem, then run the installer again to
register a fresh ephemeral runner. The installer requires a fresh instance or
install path that does not exist yet; restarting `run.sh` in the previous install
directory does not replace registration. Deregistration does **not** erase the
machine. Do not share this environment between unrelated repositories or
concurrent jobs.

Enable the `CI_RUNNER` repository variable only after auditing every workflow for
owner-only routing in the private repository. Remove that variable to return to
hosted runners. If the variable points to a label whose only matching runner has
deregistered, eligible jobs remain queued until a new matching runner is
registered. Alternatively, unset `CI_RUNNER` and rerun the affected workflow so
runner selection uses a hosted runner; changing the variable does not move a job
that is already queued. If a repository becomes public, remove the variable **and**
unregister its runner before accepting public workflows or PRs.

Dispatcher templates stay on hosted runners. Self-hosting the dispatcher requires
additional credential and process isolation managed by the operator. No runner
was installed or registered as part of developing this template.
