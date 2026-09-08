#!/usr/bin/env python3
"""
Static check on `packaging/installer.iss`, so Inno's syntax errors are found
here rather than twenty minutes into a Windows CI run.

There is no Inno Setup compiler on macOS and no way to try the script before
pushing a tag, which makes a typo in it a very expensive mistake: a full build
runs, the packaging succeeds, and it dies at the last step. Two rules catch the
mistakes actually made:

* **No `{ }` comments in `[Code]`.** Inno's Pascal uses braces for comments,
  so a comment mentioning `{app}` ends at that brace and the remainder of the
  sentence is compiled. This is not hypothetical - it is how the first build
  failed. Use `//`.
* **Pure ASCII.** Inno 6 reads a script with no BOM as ANSI, so an em-dash in
  a message becomes mojibake in the installer a visitor sees.

Run by `tools/build_app.py` before it shells out to ISCC, and standalone:

    uv run tools/check_installer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "packaging" / "installer.iss"


def code_section(text: str) -> tuple[int, str]:
    """(line number the section starts on, its text)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == "[code]":
            return i + 2, "\n".join(lines[i + 1:])
    return 0, ""


def brace_comments(code: str, first_line: int) -> list[str]:
    """
    Every `{` in `[Code]` that is not inside a string literal.

    Pascal string literals are single-quoted, so a `{` inside `'...'` is data
    (every `ExpandConstant('{app}')` in the file) and a `{` outside one opens a
    comment.

    `//` comments have to be skipped rather than scanned, and not as a nicety:
    an apostrophe in ordinary prose - "Inno's file list", "the target's
    contents" - would otherwise flip the string flag and desync every quote
    after it. The first version of this checker had exactly that bug and
    reported two confident false positives.
    """
    problems: list[str] = []
    in_string = False
    in_comment = False
    line_no = first_line
    for i, ch in enumerate(code):
        if ch == "\n":
            line_no += 1
            in_comment = False
            continue
        if in_comment:
            continue
        if ch == "'":
            in_string = not in_string
        elif ch == "/" and not in_string and code[i:i + 2] == "//":
            in_comment = True
        elif ch == "{" and not in_string:
            column = i - code.rfind("\n", 0, i)
            snippet = code[i:code.find("\n", i)].strip()[:60]
            problems.append(
                f"line {line_no}, column {column}: brace comment in [Code] - "
                f"use // instead\n    {snippet}")
    return problems


def main() -> int:
    if not ISS.exists():
        print(f"  x {ISS} is missing", file=sys.stderr)
        return 1
    raw = ISS.read_bytes()
    problems: list[str] = []

    non_ascii = {b for b in raw if b > 127}
    if non_ascii:
        text = raw.decode("utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if any(ord(c) > 127 for c in line):
                bad = "".join(sorted({c for c in line if ord(c) > 127}))
                problems.append(f"line {n}: non-ASCII ({bad}) - Inno needs a "
                                f"UTF-8 BOM to read these correctly")

    text = raw.decode("utf-8", errors="replace")
    first_line, code = code_section(text)
    if not code:
        problems.append("no [Code] section found")
    else:
        problems.extend(brace_comments(code, first_line))
        if code.count("begin") != code.count("end;") + code.count("end."):
            problems.append(
                f"begin/end look unbalanced: {code.count('begin')} begin, "
                f"{code.count('end;') + code.count('end.')} end")

    if problems:
        print(f"{ISS.relative_to(ROOT)}:", file=sys.stderr)
        for p in problems:
            print(f"  x {p}", file=sys.stderr)
        return 1
    print(f"{ISS.relative_to(ROOT)}: ok - ASCII, no brace comments in [Code]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
