"""Check repository-local link targets in Markdown documents."""

import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


DESTINATION = r"(?:<([^<>\n]*)>|((?:\\[()]|[^()\s<>]|\([^()\s]*\))*))"
TITLE = r"""(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^\)\n]*\)))?"""
INLINE_LINK = re.compile(r"(?<!\\)\[[^\]\n]*\]\([ \t]*" + DESTINATION + TITLE + r"[ \t]*\)")
REFERENCE = re.compile(r"^ {0,3}\[(?!\^)[^\]\n]+\]:[ \t]*" + DESTINATION + r"(?=[ \t]|$)")
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def markdown_links(document):
    fence = None
    for number, line in enumerate(document.splitlines(), 1):
        marker = FENCE.match(line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue

        line = INLINE_CODE.sub(" ", line)
        reference = REFERENCE.match(line)
        matches = [reference] if reference else INLINE_LINK.finditer(line)
        for match in matches:
            yield number, match[1] if match[1] is not None else match[2]


def main():
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=root,
    ).split(b"\0")

    documents = checked = broken = 0
    for name in dict.fromkeys(os.fsdecode(name) for name in names if name):
        document = root / name
        if not document.is_file():
            continue
        documents += 1
        for number, destination in markdown_links(document.read_text(encoding="utf-8")):
            link = urlsplit(destination)
            if link.scheme or link.netloc or not link.path:
                continue

            path = unquote(link.path).replace(r"\(", "(").replace(r"\)", ")")
            target = root / path.lstrip("/") if path.startswith("/") else document.parent / path
            target = target.resolve()
            checked += 1
            if not target.is_relative_to(root) or not target.exists():
                print(f"{name}:{number}: Broken local link: {destination}")
                broken += 1

    print(f"check-links: {checked} local links checked in {documents} Markdown files ({broken} broken)")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
