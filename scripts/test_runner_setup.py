"""Exercise the runner installer using fake download and registration commands."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

INSTALLER = Path(__file__).resolve().parent / "runner/setup.sh"

FAKE_COMMANDS = r'''
id() { printf '%s\n' "${SF_FAKE_UID:-1000}"; }
uname() { if [ "$1" = -s ]; then printf '%s\n' "${SF_FAKE_OS:-Linux}"; else printf '%s\n' "${SF_FAKE_ARCH:-x86_64}"; fi; }
gh() {
  case "$*" in
    *registration-token*) printf 'register\n' >> "$SF_FAKE_CALLS"; printf 'registration-token\n' ;;
    *) printf 'metadata\n' >> "$SF_FAKE_CALLS"; printf '%s\n' "${SF_FAKE_VISIBILITY:-private}" ;;
  esac
}
curl() {
  printf 'download %s\n' "${!#}" >> "$SF_FAKE_CALLS"
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --output ]; then shift; printf 'archive\n' > "$1"; fi
    shift
  done
}
sha256sum() {
  cat >/dev/null
  printf 'checksum\n' >> "$SF_FAKE_CALLS"
  [ "${SF_FAKE_BAD_SHA:-0}" = 0 ]
}
tar() {
  local destination=""
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --directory ]; then shift; destination="$1"; fi
    shift
  done
  cat > "$destination/config.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$SF_FAKE_CONFIG_ARGS"
printf '%s' "${GH_TOKEN:-missing}" > "$SF_FAKE_CONFIG_AUTH"
printf '%s' "${ACTIONS_RUNNER_INPUT_TOKEN:-missing}" > "$SF_FAKE_CONFIG_TOKEN"
printf '%s\n' "${ACTIONS_RUNNER_INPUT_TOKEN:-missing}"
exit "${SF_FAKE_CONFIG_EXIT:-0}"
EOF
  printf '#!/usr/bin/env bash\nexit 0\n' > "$destination/run.sh"
  chmod +x "$destination/config.sh" "$destination/run.sh"
}
export -f id uname gh curl sha256sum tar
bash "$1" "${@:2}"
'''


class RunnerSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / "runner"
        self.calls = self.root / "calls.txt"
        self.env = {**os.environ, "GH_TOKEN": "bootstrap-token",
                    "SF_FAKE_CALLS": (self.root / "calls.txt").as_posix(),
                    "SF_FAKE_CONFIG_ARGS": (self.root / "config-args.txt").as_posix(),
                    "SF_FAKE_CONFIG_AUTH": (self.root / "config-auth.txt").as_posix(),
                    "SF_FAKE_CONFIG_TOKEN": (self.root / "config-token.txt").as_posix()}

    def invoke(self, extra=(), env=None, owner=True):
        args = ["--repo", "example-game", "--install-dir", self.directory.as_posix(),
                "--version", "2.999.0", "--sha256", "a" * 64]
        if owner:
            args += ["--owner", "example-owner"]
        return subprocess.run(["bash", "-c", FAKE_COMMANDS, "runner-test", INSTALLER.as_posix(), *args, *extra],
                              cwd=self.root, env={**self.env, **(env or {})}, capture_output=True,
                              text=True, encoding="utf-8", timeout=15)

    def test_private_ephemeral_setup_checks_checksum_and_does_not_start_service(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = (self.root / "config-args.txt").read_text().splitlines()
        self.assertIn("--ephemeral", args)
        self.assertIn("https://github.com/example-owner/example-game", args)
        self.assertIn("soloforge-owner", args)
        self.assertNotIn("--token", args)
        self.assertEqual((self.root / "config-token.txt").read_text(), "registration-token")
        self.assertEqual((self.root / "config-auth.txt").read_text(), "missing")
        calls = self.calls.read_text().splitlines()
        self.assertEqual(calls[0], "metadata")
        self.assertTrue(calls[1].startswith("download https://github.com/actions/runner/"))
        self.assertEqual(calls[2:], ["checksum", "register"])
        self.assertNotIn("bootstrap-token", result.stdout + result.stderr)
        self.assertNotIn("registration-token", result.stdout + result.stderr)

    def test_public_repository_is_refused_before_download(self):
        result = self.invoke(env={"SF_FAKE_VISIBILITY": "public"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Only private repositories", result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), ["metadata"])
        self.assertFalse(self.directory.exists())

    def test_bad_checksum_prevents_extraction_and_registration(self):
        result = self.invoke(env={"SF_FAKE_BAD_SHA": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum did not match", result.stderr)
        self.assertNotIn("register", self.calls.read_text())
        self.assertFalse(self.directory.exists())

    def test_existing_data_is_preserved_and_registration_not_attempted(self):
        self.directory.mkdir()
        marker = self.directory / "keep.txt"
        marker.write_text("existing data", encoding="utf-8")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not exist", result.stderr)
        self.assertEqual(marker.read_text(), "existing data")
        self.assertFalse(self.calls.exists())

    def test_owner_must_be_explicit_or_from_environment(self):
        result = self.invoke(owner=False, env={"SOLOFORGE_OWNER": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--owner or SOLOFORGE_OWNER is required", result.stderr)
        self.assertFalse(self.calls.exists())
        result = self.invoke(owner=False, env={"SOLOFORGE_OWNER": "example-owner"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_root_and_unsupported_hosts_are_refused(self):
        for env in ({"SF_FAKE_UID": "0"}, {"SF_FAKE_OS": "Darwin"}, {"SF_FAKE_ARCH": "unsupported"}):
            with self.subTest(env=env):
                result = self.invoke(env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls.exists())

    def test_arm64_archive_selection(self):
        result = self.invoke(env={"SF_FAKE_ARCH": "aarch64"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("actions-runner-linux-arm64-2.999.0.tar.gz", self.calls.read_text())

    def test_missing_bootstrap_token_does_not_use_cached_login(self):
        result = self.invoke(env={"GH_TOKEN": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GH_TOKEN is required", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_invalid_digest_or_option_is_rejected_before_commands(self):
        for extra in (("--sha256", "invalid"), ("--version", "latest"), ("--public",)):
            with self.subTest(extra=extra):
                result = self.invoke(extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls.exists())

    def test_existing_empty_directory_is_refused_without_modification(self):
        self.directory.mkdir()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not exist", result.stderr)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertFalse(self.calls.exists())

    def test_caller_registration_token_uses_environment_without_requesting_another(self):
        result = self.invoke(env={"RUNNER_REGISTRATION_TOKEN": "caller-registration-token"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("register", self.calls.read_text().splitlines())
        self.assertEqual((self.root / "config-token.txt").read_text(), "caller-registration-token")
        self.assertNotIn("caller-registration-token", result.stdout + result.stderr)

    def test_config_failure_does_not_echo_the_registration_token(self):
        result = self.invoke(env={"SF_FAKE_CONFIG_EXIT": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Runner registration failed", result.stderr)
        self.assertNotIn("registration-token", result.stdout + result.stderr)

    def test_interface_and_shell_syntax(self):
        result = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(INSTALLER.with_name("setup-runner.sh").exists())
        result = subprocess.run(["bash", str(INSTALLER), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--owner", "--repo", "--install-dir", "--version", "--sha256"):
            self.assertIn(option, result.stdout)


if __name__ == "__main__":
    unittest.main()
