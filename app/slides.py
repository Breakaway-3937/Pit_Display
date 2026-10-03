"""
The slide model — what a stop in the standard rotation actually is.

The rotation used to be a list of `(title, body)` tuples and one template. The
visual system now has **four archetypes sharing one chassis**, chosen by the
shape of the content rather than by slide number:

| kind | what it is | where the red goes |
|---|---|---|
| `statement` | authored copy — a headline and a sentence | the Trace |
| `figure` | one real number out of a robot log | the `FROM LOG` seal |
| `roster` | a grid of sponsor marks | **nowhere** — the marks are the colour |
| `board` | the live diagnostics board | a latched fault, or nothing |

A surface is allowed zero red; it is never allowed two. That is why `roster`
spends none — a sponsor mark brings its own colour and would be the second.

`board` is not built here: it is a whole widget (`DiagnosticsOverlay` /
`RobotInfoOverlay`) that the presentation screen swaps in as the cycle's last
stop. It appears in this module only as the kind a picker row can carry.

Everything that produced tuples still can — `Slide.of()` and `coerce()` take
them — so a new authored slide is one line and only needs the extra fields when
it wants a different archetype.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STATEMENT = "statement"
FIGURE    = "figure"
ROSTER    = "roster"
BOARD     = "board"


@dataclass(frozen=True)
class Slide:
    """One stop in a rotation."""

    title: str
    body: str = ""
    kind: str = STATEMENT
    # Chakra caps above the headline. Names the slide's subject, not the team —
    # the team is already in the header band on every surface.
    eyebrow: str = ""
    # `figure` archetype: the numeral is the headline and the sentence is the
    # caption, so these carry the number and its unit separately.
    figure: str = ""
    unit: str = ""
    # `roster` archetype: the cells of the grid.
    items: tuple[str, ...] = field(default_factory=tuple)
    # A stop that shows a whole face instead of a slide (`app/program.py`):
    # the content key of the page, e.g. "diagnostics", "schedule".
    face: str = ""
    # Who the words belong to, when it isn't us: the ledger shows it in place
    # of "ROTATION A" (TBA facts: "Powered by The Blue Alliance").
    credit: str = ""
    # A stable name for an authored slide (app/wording.py keys its editable
    # words by it; app/program.py pairs by it). Titles can be edited; keys can't.
    key: str = ""

    @classmethod
    def of(cls, value) -> "Slide":
        """Accept a Slide, or the (title, body) tuple the rotation used to be."""
        if isinstance(value, Slide):
            return value
        title, body = value
        return cls(title=title, body=body)

    @property
    def is_from_log(self) -> bool:
        """True for slides built out of an imported log rather than authored."""
        return self.kind == FIGURE


def coerce(values) -> list[Slide]:
    return [Slide.of(v) for v in values]


def register_words(slides, screen_label: str) -> None:
    """Make each keyed slide's eyebrow, title and body admin-editable."""
    from app import wording
    group = f"Overhead slides · {screen_label}"
    for s in slides:
        if not s.key:
            continue
        wording.field(f"slide/{s.key}/eyebrow", s.eyebrow, f"{s.title}: eyebrow", group, 32)
        wording.field(f"slide/{s.key}/title", s.title, f"{s.title}: headline", group, 48)
        if s.body:
            wording.field(f"slide/{s.key}/body", s.body, f"{s.title}: sentence", group,
                          160, multiline=True)


def worded(slide: Slide) -> Slide:
    """The slide with any admin edits to its words applied."""
    if not slide.key:
        return slide
    from dataclasses import replace
    from app import wording
    return replace(slide,
                   eyebrow=wording.text(f"slide/{slide.key}/eyebrow") or slide.eyebrow,
                   title=wording.text(f"slide/{slide.key}/title") or slide.title,
                   body=wording.text(f"slide/{slide.key}/body") if slide.body else slide.body)


# ── Pulling the numeral out of a title ──────────────────────────────────────
# `1,234.5` optionally followed by a unit that belongs to the number rather
# than to the sentence: a percent sign or a degree reading, both of which read
# wrong with a space in front of them.
_FIGURE_RE = re.compile(r"^([\d,]+(?:\.\d+)?)\s*(%|°[CF])?\s*(.*)$")


def split_figure(title: str) -> tuple[str, str, str]:
    """
    Split "187 Amps" into ("187", "Amps", "187 Amps").

    Returns `("", "", title)` when the title does not lead with a number, which
    is the caller's signal that this is a statement and not a figure.
    """
    m = _FIGURE_RE.match(title.strip())
    if not m:
        return "", "", title
    number, tight_unit, rest = m.groups()
    unit = tight_unit or rest.strip()
    return number, unit, title
