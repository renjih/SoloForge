#!/usr/bin/env bash
# Configure one ephemeral runner on a dedicated, unprivileged Linux machine.
set +x
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: setup.sh --owner OWNER --repo REPOSITORY --install-dir DIRECTORY
                       --version VERSION --sha256 DIGEST [--label LABEL] [--name NAME]

SOLOFORGE_OWNER may supply the owner. GH_TOKEN must contain a short-lived token
with permission to register a runner in the named private repository.
The installer verifies the archive, configures an ephemeral runner, and exits.
It does not start the runner, install a service, or enable CI_RUNNER.
EOF
}
die() { printf 'Runner setup failed: %s\n' "$*" >&2; exit 1; }

owner="${SOLOFORGE_OWNER:-}"
repository=""
install_dir=""
version=""
digest=""
label="soloforge-owner"
runner_name=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --owner|--repo|--install-dir|--version|--sha256|--label|--name)
      [ "$#" -ge 2 ] || die "An option value is missing"
      case "$1" in
        --owner) owner="$2" ;;
        --repo) repository="$2" ;;
        --install-dir) install_dir="$2" ;;
        --version) version="$2" ;;
        --sha256) digest="$2" ;;
        --label) label="$2" ;;
        --name) runner_name="$2" ;;
      esac
      shift 2 ;;
    *) die "Unknown option; use --help" ;;
  esac
done
[[ "$owner" =~ ^[A-Za-z0-9_.-]+$ ]] || die "--owner or SOLOFORGE_OWNER is required"
[[ "$repository" =~ ^[A-Za-z0-9_.-]+$ ]] || die "--repo must name one repository"
[ -n "$install_dir" ] || die "--install-dir is required"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "--version must be an explicit runner release version"
[[ "$digest" =~ ^[[:xdigit:]]{64}$ ]] || die "--sha256 must be the release archive's 64-character digest"
[[ "$label" =~ ^[A-Za-z0-9._-]+$ ]] || die "--label must be a simple runner label"
runner_name="${runner_name:-soloforge-$repository}"
[[ "$runner_name" =~ ^[A-Za-z0-9._-]+$ ]] || die "--name must be a simple runner name"
[ "$(id -u)" -ne 0 ] || die "Run as a dedicated non-root account"
[ "$(uname -s)" = Linux ] || die "This installer supports Linux only"
case "$(uname -m)" in
  x86_64) architecture=x64 ;;
  aarch64|arm64) architecture=arm64 ;;
  *) die "Unsupported Linux architecture" ;;
esac
for command in gh curl sha256sum tar realpath mktemp; do
  command -v "$command" >/dev/null || die "A required command is unavailable: $command"
done
[ -n "${GH_TOKEN:-}" ] || die "GH_TOKEN is required; cached personal logins are not used for bootstrap"
if [ -e "$install_dir" ] || [ -L "$install_dir" ]; then
  die "The install directory must not exist; choose a fresh directory"
fi
install_dir="$(realpath -m -- "$install_dir")"
[ "$install_dir" != / ] || die "The filesystem root cannot be an install directory"
if [ -e "$install_dir" ] || [ -L "$install_dir" ]; then
  die "The install directory must not exist; choose a fresh directory"
fi
visibility="$(gh api --hostname github.com "repos/$owner/$repository" --jq .visibility 2>/dev/null)" || die "Repository visibility could not be verified"
[ "$visibility" = private ] || die "Only private repositories are supported; use hosted runners for public repositories"

archive="$(mktemp)"
trap 'rm -f -- "$archive"' EXIT
url="https://github.com/actions/runner/releases/download/v$version/actions-runner-linux-$architecture-$version.tar.gz"
curl --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 20 --max-time 300 \
  --fail --location --silent --show-error --output "$archive" "$url" 2>/dev/null || die "Runner archive download failed"
printf '%s  %s\n' "$digest" "$archive" | sha256sum --check --status || die "Runner archive checksum did not match"
mkdir -- "$install_dir" || die "The fresh install directory could not be created"
tar --extract --gzip --file "$archive" --directory "$install_dir" --no-same-owner 2>/dev/null || die "Runner archive could not be extracted"
[ -x "$install_dir/config.sh" ] && [ -x "$install_dir/run.sh" ] || die "Runner release is missing executable entrypoints"
registration_token="${RUNNER_REGISTRATION_TOKEN:-}"
if [ -z "$registration_token" ]; then
  registration_token="$(gh api --hostname github.com --method POST "repos/$owner/$repository/actions/runners/registration-token" --jq .token 2>/dev/null)" || die "Runner registration token was unavailable"
fi
[ -n "$registration_token" ] || die "Runner registration token was unavailable"
if ! (
  cd -- "$install_dir"
  unset GH_TOKEN GITHUB_TOKEN SF_GITHUB_TOKEN SF_AUTH_SECRET_TOKEN RUNNER_REGISTRATION_TOKEN
  unset CODEX_AUTH_JSON OPENAI_API_KEY CODEX_API_KEY ANTHROPIC_API_KEY
  export ACTIONS_RUNNER_INPUT_TOKEN="$registration_token"
  exec ./config.sh --unattended --ephemeral --url "https://github.com/$owner/$repository" \
    --name "$runner_name" --labels "$label" --work _work
) >/dev/null 2>&1; then
  unset registration_token
  die "Runner registration failed; inspect diagnostics on the isolated machine"
fi
unset registration_token
printf 'Configured one-job runner for %s/%s with label %s.\n' "$owner" "$repository" "$label"
printf 'Start run.sh from a clean environment after reviewing owner-only workflow routing.\n'
