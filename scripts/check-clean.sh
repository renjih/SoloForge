#!/usr/bin/env bash
# Scan repository text for common disclosure patterns.
set -euo pipefail

python3 - "${BASH_SOURCE[0]}" <<'PY'
import os
from pathlib import Path
import re
import subprocess
import sys


PATTERNS = {
    "private key header": re.compile(rb"BEGIN .*PRIVATE KEY"),
    "token prefix": re.compile(rb"(?<![A-Za-z0-9_])(?:ghp_|github_pat_|sk-|AKIA)"),
    "spreadsheet link": re.compile(rb"spreadsheets/d/"),
    "local path": re.compile(rb"/Users/|C:\\+Users(?![A-Za-z0-9_])"),
}
HOME_PATH = re.compile(rb"/home/([A-Za-z0-9_.-]+)")
RUNNER_USERS = {b"runner", b"runneradmin", b"ghrunner"}
EMAIL = re.compile(
    rb"(?<![a-z0-9.!#$%&'*+/=?^_\x60{|}~-])"
    rb"([a-z0-9.!#$%&'*+/=?^_\x60{|}~-]{1,64})@([a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,})(?![a-z0-9-])",
    re.IGNORECASE,
)
EXAMPLE_DOMAINS = (b"example.com", b"example.invalid")


def allowed_email(local, domain):
    local, domain = local.lower().lstrip(b"'\x60"), domain.lower()
    return (
        local.split(b"+", 1)[0] == b"noreply"
        or b"noreply" in domain.split(b".")
        or any(domain == example or domain.endswith(b"." + example) for example in EXAMPLE_DOMAINS)
    )


def findings(line):
    for kind, pattern in PATTERNS.items():
        if pattern.search(line):
            yield kind
    if any(match[1] not in RUNNER_USERS for match in HOME_PATH.finditer(line)):
        yield "local home path"
    if b"@" in line and any(not allowed_email(*match.groups()) for match in EMAIL.finditer(line)):
        yield "email address"


def text_lines(path):
    if path.is_symlink():
        yield from os.fsencode(os.readlink(path)).splitlines()
    elif path.is_file():
        with path.open("rb") as stream:
            if b"\0" not in stream.read(8192):
                stream.seek(0)
                yield from stream


root = Path(os.fsdecode(subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).rstrip(b"\n"))).resolve()
scanner = Path(sys.argv[1]).resolve()
files = subprocess.check_output(
    ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
).split(b"\0")

count = 0
for name in dict.fromkeys(os.fsdecode(file) for file in files if file):
    path = root / name
    if path == scanner:
        continue
    for number, line in enumerate(text_lines(path), 1):
        for kind in findings(line):
            print(f"{name!r}:{number}: {kind}")
            count += 1

print(f"check-clean: {count} matches")
sys.exit(1 if count else 0)
PY
