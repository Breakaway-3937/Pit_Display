"""
The Slides: everything the overhead screens show visitors, one subject at a time.

Brayden, 2026-11-17: "I would want a load of info (say 50+ slides) … the same
base layout of A or B being the detail and the opposing being the
description … they have to be a matched set so that 'Quality Award' is the
only thing being shown on the screens." Every outward-facing piece of
information is a **Slide**, whatever its source (home's TBA datasets and
facts, Breakaway's records, the robot's logs, the team's own numbers), and a
Slide fills **both** overhead screens:

| side | what | drawn by |
|---|---|---|
| **Description** | eyebrow, the Trace, a bold headline, one catch line | the statement archetype (`slide_panel.py`) |
| **Detail** | the information itself, laid out for its shape | a face: `quality` (ranked bars), `bk_seasons` (a season chart), `dataset` (a table), `fact_card` (a figure, a few figures, or a sentence) |

**Sides swap, except under an unchanging Description** (Brayden: "varied
between which screen is the Detail and which is the Description"; then
2026-11-17: "the Breakaway header and the Across Arkansas aspects … need to
be in the same layout", "if the section is changing the Description … it's
encouraged to have the slides swap. It's just when the description page
isn't changing that I'd want it to be stable"). A section's Slides run
together; a Slide whose Description is the same as the one before keeps its
layout (the facts under one header: the Description doesn't move, the
Detail dissolves to the next fact, `presentation_base._Dissolve` and the
page's `render`); any other Slide swaps screens. An admin can pin one Slide
(`deck/<key>/side`).

**Words: shipped from the data, edited by the team** (his choice,
2026-11-17). Each Description's eyebrow, headline and catch is a
`wording` field (`deck/<key>/…`, under "Slides · <group>" in Screen
Wording) whose default code builds from the Slide's own data; an edit
replaces it on every pit. **Every number is the data's**, never composed
beyond counting and summing home's rows.

**Not Slides**: Robot diagnostics, Next match, Checklists, Analysis. Those
are the crew's own screens, chosen on the control screen; the rotation is
Slides only.

Order: section by section (our team, team stats, Breakaway's records, the
facts about Breakaway, the facts across Arkansas, each dataset). A Slide
appears only when its data does: home's datasets once an adult has cleared
them, the robot's once a log is imported.

No Qt here: the native screens and the pit-network page read the same deck.
"""

from __future__ import annotations

from dataclasses import dataclass
from app.attribution import TBA_TEXT

# The faces a Detail can be (app/windows/presentation_base.py pages).
DETAIL_FACES = ("quality", "bk_seasons", "dataset", "fact_card")


@dataclass(frozen=True)
class Item:
    """One Slide: a Description and a Detail about one subject."""
    key: str                 # stable, for words, pins and the pickers
    group: str               # where it's listed ("Team stats", "Did you know?")
    eyebrow: str             # the Description's words, as shipped
    headline: str
    catch: str
    detail: str              # the Detail's face (DETAIL_FACES)
    ref: str = ""            # what the Detail shows: a dataset key, a card key
    credit: str = ""         # whose data, for the ledger
    side: str = ""           # "a" / "b": the Description's screen when pinned
    section: str = ""        # Slides that run together in one layout ("" = the group)
    summary: str = ""        # what this one is about, for lists (the fact, the record)

    @property
    def in_section(self) -> str:
        return self.section or self.group


# ── Words ────────────────────────────────────────────────────────────────────

def _words(item: Item) -> Item:
    """Register the Description's words as editable and apply any edit."""
    from dataclasses import replace
    from app import wording
    group = f"Slides · {item.group}"
    k = f"deck/{item.key}"
    name = item.headline[:40] or item.key
    eyebrow = wording.field(f"{k}/eyebrow", item.eyebrow, f"{name}: eyebrow", group, 32)
    headline = wording.field(f"{k}/headline", item.headline, f"{name}: headline", group, 64)
    catch = wording.field(f"{k}/catch", item.catch, f"{name}: catch line", group, 160,
                          multiline=True)
    side = wording.field(f"{k}/side", "auto",
                         f"{name}: Description on screen A, B or auto", group, 4)
    pinned = side.strip().lower()
    return replace(item, eyebrow=eyebrow, headline=headline, catch=catch,
                   side=pinned if pinned in ("a", "b") else "")


# ── Sources ──────────────────────────────────────────────────────────────────

def _team() -> list[Item]:
    """The team's own Slides: who we are, our reach, our record, the robot."""
    out = [Item("welcome", "Breakaway", "Welcome", "Welcome to Breakaway",
                "Stop by and meet Team 3937 — we'd love to tell you about our season.",
                "fact_card", "card:reach")]
    if _season_totals():
        first = _first_season()
        out.append(Item("awards", "Breakaway", "Recognition", "Awards & Milestones",
                        (f"Every season since {first}, from The Blue Alliance." if first
                         else "Every season, from The Blue Alliance."),
                        "fact_card", "card:seasons", TBA_TEXT))
    if _robot_figures():
        out.append(Item("robot", "Breakaway", "The robot", "Our Robot This Year",
                        "Designed and built from the ground up by our student members.",
                        "fact_card", "card:robot"))
    return out


def _stats() -> list[Item]:
    from app import datasets
    out = []
    q = datasets.get("quality")
    if q is not None:
        out.append(Item("quality", "Team stats", "Quality Award", q.title or "Quality Award leaders",
                        _quality_catch(q), "quality", "quality", TBA_TEXT))
    s = datasets.get("bk_seasons")
    if s is not None:
        out.append(Item("bk_seasons", "Team stats", "Season by season",
                        "Breakaway, season by season", _seasons_catch(),
                        "bk_seasons", "bk_seasons", TBA_TEXT))
    return out


def _datasets() -> list[Item]:
    from app import datasets, tba_facts
    out = []
    for d in datasets.generic():
        eyebrow = tba_facts.title(d.category) if d.category else "Team stats"
        out.append(Item(f"dataset:{d.key}", "Datasets", eyebrow, d.title, d.description,
                        "dataset", d.key, TBA_TEXT, section=f"Datasets · {d.title}"))
    return out


def _records() -> list[Item]:
    from app import datasets
    out = []
    for i, (record, value, detail) in enumerate(datasets.records()):
        out.append(Item(f"record:{_slug(record)}", "Breakaway's records", "Breakaway's records",
                        record, detail, "fact_card", f"record:{i}", TBA_TEXT,
                        summary=f"{value} · {record}"))
    return out


def _facts() -> list[Item]:
    from app import tba_facts
    out = []
    for f in tba_facts.facts():
        out.append(Item(f"fact:{f.key}", "Did you know?", "Did you know?",
                        tba_facts.title(f.category),
                        "From The Blue Alliance's history of every FRC event.",
                        "fact_card", f"fact:{f.key}", TBA_TEXT,
                        section=f"Did you know? · {tba_facts.title(f.category)}",
                        summary=f.text))
    return out


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "x"


# ── The deck ─────────────────────────────────────────────────────────────────

def items() -> list[Item]:
    """Every Slide that has its data, section by section, words applied.
    A source that fails leaves its Slides out; it never takes the deck down."""
    order: list[Item] = []
    for source in (_team, _stats, _records, _facts, _datasets):
        try:
            order += source()
        except Exception:
            pass
    return [_words(it) for it in order]


def sides(found: list[Item]) -> list[bool]:
    """Per Slide, is its Description on A? **The screens swap every Slide,
    except when the Description doesn't change** (Brayden, 2026-11-17: "It's
    just when the description page isn't changing that I'd want it to be
    stable"): a run of facts under one "Did you know? · Breakaway" header
    holds its layout while the Detail changes; a run of records, each with
    its own headline, swaps. The first is on A; a pinned Slide keeps its pin."""
    out, prev, on_a = [], None, False
    for it in found:
        same = prev is not None and (it.eyebrow, it.headline, it.catch) == \
            (prev.eyebrow, prev.headline, prev.catch)
        if not same:
            on_a = not on_a
        out.append(it.side == "a" if it.side else on_a)
        prev = it
    return out


# ── Detail cards (the `fact_card` face) ──────────────────────────────────────

def card(ref: str) -> dict | None:
    """What a `fact_card` Detail shows for `ref`:
    `{"kind": "record", value, record, detail}`, `{"kind": "figures", eyebrow,
    figures: [{value, label}]}` or `{"kind": "fact", eyebrow, text}`."""
    from app import datasets, tba_facts
    kind, _, key = ref.partition(":")
    if kind == "record":
        recs = datasets.records()
        try:
            rec, val, detail = recs[int(key)]
        except (ValueError, IndexError):
            return None
        return {"kind": "record", "record": rec, "value": val, "detail": detail,
                "eyebrow": "Breakaway's records", "credit": TBA_TEXT}
    if kind == "fact":
        f = next((f for f in tba_facts.facts() if f.key == key), None)
        if f is None:
            return None
        return {"kind": "fact", "category": f.category, "text": f.text, "plain": True,
                "credit": TBA_TEXT,
                "eyebrow": "Did you know?" + ("  ·  Arkansas" if f.category == "arkansas" else "")}
    if kind == "card":
        figs = {"reach": _reach_figures, "seasons": _season_totals,
                "robot": _robot_figures}.get(key, lambda: [])()
        if not figs:
            return None
        first = _first_season()
        eyebrow = {"reach": "Our reach",
                   "seasons": f"Since {first}" if first else "Every season",
                   "robot": "From the robot's logs"}[key]
        return {"kind": "figures", "eyebrow": eyebrow, "figures": figs,
                "credit": TBA_TEXT if key == "seasons" else ""}
    return None


def _reach_figures() -> list[dict]:
    """The team's own numbers, as the pit-front panel shows them (its words,
    edited there: `board/stat/<n>/…`)."""
    from app import wording
    import app.widgets.interactive_board  # noqa: F401  (registers the fields)
    out = []
    for i in range(4):
        value = wording.text(f"board/stat/{i}/number")
        label = wording.text(f"board/stat/{i}/caption")
        if value:
            out.append({"value": value, "label": label})
    return out


def _first_season() -> int | None:
    from app import datasets
    d = datasets.get("bk_seasons")
    years = [d.value(r, "year") for r in d.rows] if d is not None else []
    years = [y for y in years if isinstance(y, int)]
    return min(years) if years else None


def _season_totals() -> list[dict]:
    """Seasons, events, matches won and awards, summed from home's `bk_seasons`."""
    from app import datasets
    d = datasets.get("bk_seasons")
    if d is None or not d.rows:
        return []

    def total(col: str) -> int | None:
        vals = [d.value(r, col) for r in d.rows]
        nums = [v for v in vals if isinstance(v, (int, float))]
        return int(sum(nums)) if nums else None
    out = [{"value": str(len(d.rows)), "label": "Seasons"}]
    for col, label in (("events", "Events played"), ("wins", "Matches won"),
                       ("awards", "Awards")):
        v = total(col)
        if v is not None:
            out.append({"value": f"{v:,}", "label": label})
    return out


def _robot_figures() -> list[dict]:
    """What the logs hold: recordings read, motors, signals. Counted, never estimated."""
    try:
        from app.db import db
        logs = db.fetchone("SELECT COUNT(*) n FROM log_session")["n"]
        if not logs:
            return []
        motors = db.fetchone("SELECT COUNT(*) n FROM device WHERE device_type = 'TalonFX'")["n"]
        signals = db.fetchone("SELECT COUNT(DISTINCT name) n FROM signal")["n"]
    except Exception:
        return []
    out = [{"value": f"{logs:,}", "label": "Robot logs read"}]
    if motors:
        out.append({"value": f"{motors:,}", "label": "Motors watched"})
    if signals:
        out.append({"value": f"{signals:,}", "label": "Signals recorded"})
    return out


def _quality_catch(q) -> str:
    """"Breakaway: 10 Quality Awards · #5 of 1,186 teams", from home's own row."""
    from app import datasets
    row = next((r for i, r in enumerate(q.rows) if q.is_highlight(i)), None)
    if row is None:
        return q.description
    count = q.value(row, "Top_Quality")
    rank = q.value(row, "Quality_Rank")
    bits = []
    if count is not None:
        bits.append(f"{datasets.cell(count)} Quality Awards")
    if rank is not None:
        bits.append(f"#{datasets.cell(rank)} of {q.total_rows:,} teams")
    return ("Breakaway: " + " · ".join(bits)) if bits else q.description


def _seasons_catch() -> str:
    figs = {f["label"]: f["value"] for f in _season_totals()}
    if not figs:
        return "Breakaway's record, finishes and awards, season by season."
    parts = [f"{figs['Seasons']} seasons"]
    if "Matches won" in figs:
        parts.append(f"{figs['Matches won']} matches won")
    if "Awards" in figs:
        parts.append(f"{figs['Awards']} awards")
    return ", ".join(parts) + "."
