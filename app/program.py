"""
The overhead program: what Screen A and Screen B show together, stop by stop.

**The program is the Slides** (`app/deck.py`; Brayden, 2026-11-17): every
outward-facing subject is one stop, its **Description** (the statement
archetype: eyebrow, the Trace, headline, catch) on one screen and its
**Detail** (the information, on its face) on the other, the sides
alternating from one Slide to the next. Robot diagnostics, Next match,
Checklists and Analysis are not in it: they're chosen on the control screen.

The two overhead screens are one unit. One driver (`app/rotation.py`) moves
both screens to the same stop at once. **Stops come and go with their data,
on both screens together**, and `stops()` is computed fresh, so both screens
and the control screen's pickers always agree on the list and its length.

A stop's side is a `Slide`: a statement (the Description), or a whole face
(`Slide.face`, `Slide.item` naming what that face shows) for the Detail. A
screen pinned to a face (per-screen `content`) stays pinned; its position
still follows the program, so it rejoins in step.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.slides import BOARD, STATEMENT, Slide

SIDES = ("presentation_a", "presentation_b")


@dataclass(frozen=True)
class Stop:
    key: str
    a: Slide
    b: Slide

    def side(self, screen_id: str) -> Slide:
        return self.b if screen_id.endswith("_b") else self.a


def _description(item) -> Slide:
    return Slide(kind=STATEMENT, key=f"deck:{item.key}", eyebrow=item.eyebrow,
                 title=item.headline, body=item.catch, credit=item.credit,
                 section=item.in_section,
                 summary=item.summary)


def _detail(item) -> Slide:
    # The picker lists a Detail by its Slide's headline, so both screens' rows
    # read as the same subject.
    return Slide(kind=BOARD, face=item.detail, item=item.ref, eyebrow=item.group,
                 title=item.headline, credit=item.credit, section=item.in_section,
                 summary=item.summary)


def _fallback() -> Stop:
    """Before any data: the welcome, both screens, so a screen is never empty."""
    from app.slides import worded
    from app.windows.presentation_a import PresentationScreenA
    hello = worded(next(s for s in PresentationScreenA.SLIDES if s.key == "welcome"))
    return Stop("hello", hello, hello)


def stops() -> list[Stop]:
    """The program as it stands now, the same list for both screens: one stop
    per Slide, one layout per section (`deck.sides`)."""
    from app import deck
    found = deck.items()
    out: list[Stop] = []
    for item, on_a in zip(found, deck.sides(found)):
        d, x = _description(item), _detail(item)
        out.append(Stop(item.key, d, x) if on_a else Stop(item.key, x, d))
    return out or [_fallback()]


def side(screen_id: str) -> list[Slide]:
    """One screen's half of the program, in order."""
    return [s.side(screen_id) for s in stops()]


def keys() -> tuple[str, ...]:
    """What's in the program, for noticing when it changes."""
    return tuple(s.key for s in stops())
