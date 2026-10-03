"""
The overhead program: what Screen A and Screen B show together, stop by stop.

> **TO BE REVISED IN ITS ENTIRETY (Brayden, 2026-10-02).** The slide rotation
> will be totally revamped. What's here is a **minimal set of examples kept
> only to prove the machinery still works** (one stop of each kind: an
> authored slide pair, two face pairs, a fact pair); the other story pairs and
> the robot-log fact pairs were scrapped. Don't extend this list; redesign it.

The two overhead screens are one unit. Each **stop** names what A shows and
what B shows, and one driver (`app/rotation.py`) moves both screens to the
same stop at once: when A shows Robot Diagnostics, B shows Robot Info; when A
shows the next match, B shows our schedule. Before this, each screen walked
its own list on a shared timer, and lists of different lengths drifted apart.

**Stops come and go with their data, on both screens together.** No robot
log → no robot stop; no event → no event stop; no facts from home → no
"Did you know?" stops. `stops()` is computed fresh, so both screens and the
control screen's pickers always agree on the list and its length.

A stop's side is a `Slide`: an authored or generated slide, or a whole face
(`Slide.face`, e.g. "diagnostics") which the screen switches to for that stop.
A screen pinned to one face (per-screen `content`) stays pinned; its position
still follows the program, so it rejoins in step.

**The pairings below are a DRAFT** (Brayden, 2026-10-02: "draft them and
we'll figure it out later"). `STORY` and `_fact_pairs()` are the two places
to change.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.slides import BOARD, STATEMENT, Slide

SIDES = ("presentation_a", "presentation_b")

# How many "Did you know?" pairs one pass of the program shows; the next pass
# shows the next ones (`next_facts()`), so every fact comes round in turn.
FACT_PAIRS_PER_PASS = 1


@dataclass(frozen=True)
class Stop:
    key: str
    a: Slide
    b: Slide

    def side(self, screen_id: str) -> Slide:
        return self.b if screen_id.endswith("_b") else self.a


# ── EXAMPLES ONLY (see the module note): one authored pair, to prove slide
# stops still pair. (key, A's title, B's title), titles as in
# presentation_a.py / presentation_b.py.
STORY = [
    ("hello",     "Welcome to Breakaway",  "Come Ask Us Anything"),
]
# Where the data stops sit among the story stops.
DATA_AFTER = "hello"


def _face(face: str, eyebrow: str, title: str, body: str = "") -> Slide:
    return Slide(kind=BOARD, face=face, eyebrow=eyebrow, title=title, body=body)


ROBOT = Stop("robot",
             _face("diagnostics", "Live board", "Robot Diagnostics",
                   "Battery, current, temperature and latched faults from the last log."),
             _face("robot_info", "Live board", "Robot Info",
                   "What tripped, on which motor, and which log it came from."))
STATS = Stop("stats",
             _face("quality", "Team stats", "Quality Award leaders",
                   "Most Quality Awards won, every FRC team."),
             _face("bk_seasons", "Team stats", "Season by season",
                   "Breakaway's record, finishes and awards, 2012 to now."))
EVENT = Stop("event",
             _face("next_match", "Event", "Next match", "When we go, off the Nexus feed."),
             _face("schedule", "Event", "Our matches",
                   "Breakaway's matches today, results and our record."))

_fact_offset = 0


def next_facts() -> None:
    """Move the "Did you know?" stops on to the next pairs (once per pass)."""
    global _fact_offset
    _fact_offset += FACT_PAIRS_PER_PASS


def _authored() -> dict[str, Slide]:
    from app.windows.presentation_a import PresentationScreenA
    from app.windows.presentation_b import PresentationScreenB
    out = {}
    for s in list(PresentationScreenA.SLIDES) + list(PresentationScreenB.SLIDES):
        slide = Slide.of(s)
        out.setdefault(slide.title, slide)
    return out


def _fact_slide(text: str, credit: str) -> Slide:
    return Slide(kind=STATEMENT, eyebrow="Did you know?", title=text, credit=credit)


def _fact_pairs() -> list[Stop]:
    """
    DRAFT pairing for home's facts: consecutive facts of the same category, in
    home's order (home writes them topic by topic: awards, streak, record,
    playoffs, robots, rivals), so a pair reads as one thought across the two
    screens. A lone last fact pairs with the category's first.
    """
    from app import tba_facts
    facts = tba_facts.facts()
    if not facts:
        return []
    pairs = []
    for cat in dict.fromkeys(f.category for f in facts):
        group = [f for f in facts if f.category == cat]
        for i in range(0, len(group), 2):
            a = group[i]
            b = group[i + 1] if i + 1 < len(group) else group[0]
            if a is b:
                continue
            pairs.append((a, b))
    if not pairs:
        return []
    start = _fact_offset % len(pairs)
    chosen = (pairs[start:] + pairs[:start])[:FACT_PAIRS_PER_PASS]
    return [Stop(f"fact:{a.key}+{b.key}", _fact_slide(a.text, tba_facts.CREDIT),
                 _fact_slide(b.text, tba_facts.CREDIT)) for a, b in chosen]


def _has_log() -> bool:
    try:
        from app.robot.diagnostics import latest_session_id
        return latest_session_id() is not None
    except Exception:
        return False


def _has_stats() -> bool:
    from app import datasets
    return (datasets.get("quality") is not None
            and datasets.get("bk_seasons") is not None)


def _has_event() -> bool:
    try:
        from app.nexus import nexus
        return bool(nexus.event_key) and bool(nexus.our_matches())
    except Exception:
        return False


def stops() -> list[Stop]:
    """The program as it stands now, the same list for both screens."""
    authored = _authored()
    out: list[Stop] = []
    for key, a_title, b_title in STORY:
        a, b = authored.get(a_title), authored.get(b_title)
        if a is not None and b is not None:
            out.append(Stop(key, a, b))
        if key == DATA_AFTER:
            if _has_log():
                out.append(ROBOT)           # robot-log fact pairs scrapped (see note)
            if _has_event():
                out.append(EVENT)
            if _has_stats():
                out.append(STATS)           # home's datasets, once cleared
            out += _fact_pairs()
    return out or [Stop("hello", Slide(title="Breakaway"), Slide(title="Breakaway"))]


def side(screen_id: str) -> list[Slide]:
    """One screen's half of the program, in order."""
    return [s.side(screen_id) for s in stops()]


def keys() -> tuple[str, ...]:
    """What's in the program, for noticing when it changes."""
    return tuple(s.key for s in stops())
