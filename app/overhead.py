"""
The overhead screens are a matched set: one content choice for the pair.

Brayden, 2026-10-02: "The overhead screens need to be a matched set at all
times." Choosing what's on the overhead screens chooses it for **both**, and
each screen shows its half:

| set | Screen A | Screen B |
|---|---|---|
| `rotation` | A's half of each program stop | B's half, in step (`app/program.py`) |
| `next_match` | Nexus queue info (Next Match board) | Breakaway's matches today, results and our record |
| `robot` | Robot Diagnostics | Robot Info |
| `checklist` | a checklist | a checklist (each screen picks its own list) |
| `facts` | Breakaway's facts | Arkansas's facts |
| `analysis` | the newest analysis board | Robot Info (the same log, motor by motor) |
| `stats` | the Quality Award leaderboard | Breakaway season by season |
| `fun` | a Breakaway record, as a figure card | a "Did you know?" sentence, as a card (turning together) |
| `datasets` | home's datasets, the first of each pair | the second, turning together |

**Shared: the set, and the rotation's position. Per screen: everything
else** — power, theme, monitor, network publishing, which checklist. A screen
switched off doesn't touch the other; switched on, it shows its half of the
current set.

Mechanically each screen still has its own `content` key (every face, the
webcast and the checks read it); this module writes both from the set, and
`app/rotation.py` mirrors a change made to either screen onto its partner, so
the pair can't be left mismatched by any path.
"""

from __future__ import annotations

SIDES = ("presentation_a", "presentation_b")

# set → (A's content, B's content), in the order the control screen lists them.
SETS: dict[str, tuple[str, str]] = {
    "rotation":   ("rotation",    "rotation"),
    "next_match": ("next_match",  "schedule"),
    "robot":      ("diagnostics", "robot_info"),
    "checklist":  ("checklist",   "checklist"),
    "facts":      ("facts",       "facts"),
    "analysis":   ("analysis",    "robot_info"),
    "stats":      ("quality",     "bk_seasons"),
    "fun":        ("fact_card",   "fact_card"),
    "datasets":   ("dataset",     "dataset"),
}
LABELS = {
    "rotation":   "Slide rotation",
    "next_match": "Next match",
    "robot":      "Robot diagnostics",
    "checklist":  "Checklists",
    "facts":      "Did you know?",
    "analysis":   "Analysis board",
    "stats":      "Team stats",
    "fun":        "Fun facts",
    "datasets":   "Datasets",
}


def half(set_name: str, screen_id: str) -> str:
    """What `screen_id` shows for `set_name`."""
    a, b = SETS.get(set_name, SETS["rotation"])
    return b if screen_id.endswith("_b") else a


def set_of(screen_id: str, content: str) -> str | None:
    """The set a screen's content belongs to, from that screen's side. B's
    Robot Info is ambiguous (robot or analysis): A decides, so ask A first."""
    side = 1 if screen_id.endswith("_b") else 0
    for name, pair in SETS.items():
        if pair[side] == content:
            return name
    return None


def current() -> str:
    """The set on screen now, read from Screen A (which is never ambiguous)."""
    from app.config import config
    return set_of(SIDES[0], str(config.get(SIDES[0], "content", "rotation"))) or "rotation"


def choose(set_name: str) -> None:
    """Put `set_name` on both overhead screens."""
    from app.config import config
    if set_name not in SETS:
        return
    for screen in SIDES:
        config.set(screen, "content", half(set_name, screen))


def partner_fix(screen: str, content: str) -> None:
    """A screen's content changed by any path: bring its partner to the
    matching half. A's change decides the set; B's only when it's unambiguous."""
    from app.config import config
    if screen not in SIDES:
        return
    other = SIDES[1] if screen == SIDES[0] else SIDES[0]
    name = set_of(screen, content)
    if screen == SIDES[1] and content == "robot_info":
        # B shows Robot Info for both "robot" and "analysis": keep A's choice
        # if it's one of them, else robot.
        name = current() if current() in ("robot", "analysis") else "robot"
    if name is None:
        return
    want = half(name, other)
    if config.get(other, "content", "rotation") != want:
        config.set(other, "content", want)
