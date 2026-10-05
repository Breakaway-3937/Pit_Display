"""
The robot brief: what this team knows about its robot and season, written once
by the crew and read by the analyst before every log.

Brayden, 2026-10-03: the model "needs to be contextualized to FRC robotics and
the concepts around each match." Handing an 8B model the game manual or the
robot code makes it worse, not better: a 16k-token context can't hold them,
and the instructions get lost in the noise. What helps is short and specific:
which mechanisms exist and what drives them, what normal looks like, what has
been going wrong, what the crew wants to hear about. That's this.

A team setting (doc `brief`, `robot_brief.json` beside the database): written
in Control → Analysis (admin), synced to every pit. Capped at `MAX_CHARS`
(~750 tokens). The template ships as the default and is never sent: until the
crew writes something, the analyst gets no brief rather than empty headings.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app import paths

FILE_NAME = "robot_brief.json"
MAX_CHARS = 3000
DEFAULTS: dict[str, Any] = {"text": ""}

TEMPLATE = """\
Season and game: (the year's game in two or three lines: how points are scored, \
what the robot does in auto, teleop and endgame, and how long each lasts)

Our robot: (each mechanism, what it does, and which motors or CAN IDs drive it, \
e.g. "Intake: roller on TalonFX 5, wrist on TalonFX 7")

What normal looks like: (temperatures, currents or voltages the crew considers \
fine, e.g. "the shooter runs near 60 A when spinning up; under 60 °C is fine")

Known issues and watch list: (what has broken or been flaky lately, and what \
to keep an eye on)

What the crew wants flagged: (what's worth a pit stop between matches, and \
what isn't)
"""


def path():
    return paths.data(FILE_NAME)


def load() -> dict[str, Any]:
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    text = stored.get("text") if isinstance(stored, dict) else ""
    return {"text": str(text or "")[:MAX_CHARS]}


def save(**changes: Any) -> dict[str, Any]:
    values = load()
    if "text" in changes:
        values["text"] = str(changes["text"] or "").strip()[:MAX_CHARS]
    try:
        path().write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    except OSError:
        pass
    return values


def _prompts() -> list[str]:
    """The template's "(…)" prompts, exactly as written."""
    return re.findall(r"\([^)]*\)", " ".join(TEMPLATE.split()))


def for_model() -> str:
    """The brief as the analyst gets it, or "" when the crew hasn't written one:
    the template's prompts are removed wherever they were left, and so are
    headings with nothing under them."""
    text = " ".join(load()["text"].split(" "))
    flat = "\n".join(" ".join(ln.split()) for ln in text.splitlines())
    for prompt in _prompts():
        flat = flat.replace(prompt, "")
    lines = [ln.rstrip() for ln in flat.splitlines()]
    headings = tuple(h.split(":", 1)[0] + ":" for h in TEMPLATE.splitlines() if ":" in h)

    def starts_section(ln: str) -> bool:
        return ln.strip().startswith(headings)

    out: list[str] = []
    for i, ln in enumerate(lines):
        if ln.strip().endswith(":"):
            nxt = next((x for x in lines[i + 1:] if x.strip()), "")
            if not nxt or starts_section(nxt) or nxt.strip().endswith(":"):
                continue                     # a heading left empty
        out.append(ln)
    result = "\n".join(out).strip()
    return re.sub(r"\n{3,}", "\n\n", result)
