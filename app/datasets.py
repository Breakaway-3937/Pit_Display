"""
Home's standard datasets (`home_dataset`, home/REQUESTS.md R10/R11).

Generic, self-describing tables home builds from TBA's history: a title, a
description, `columns`, `rows` already in display order, a `highlight` (our
row), `total_rows` ("showing 25 of 1,186"). The pit renders any of them the
same way (`dataset_overlay.py`); two have screens of their own:

| dataset | screen | why |
|---|---|---|
| `quality` | a ranked bar chart (`quality_overlay.py`) | Brayden's own view, the team's most important dataset |
| `bk_seasons` | a season-by-season time series (`seasons_overlay.py`) | a history reads as a line, not a table |

**Nothing appears until the adults turn it on** (`dataset_settings`, a team
setting). Every number is home's; the pit only orders, formats and marks.
All of it is TBA data: every screen carries "Powered by The Blue Alliance".
No Qt here: the native faces and the pit-network page read the same values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app import dataset_settings

# Datasets with a screen of their own; everything else is generic.
DEDICATED = {"quality": "quality", "bk_seasons": "bk_seasons", "bk_records": "fact_card"}
RECORDS_KEY = "bk_records"
CARD_PERIOD_S = 15


def records() -> list[tuple[str, str, str]]:
    """(record, value, detail) from home's `bk_records`, in its order, once cleared."""
    d = get(RECORDS_KEY)
    if d is None:
        return []
    out = []
    for row in d.rows:
        rec, val = d.value(row, "record"), d.value(row, "value")
        if rec and val is not None:
            out.append((str(rec), cell(val), str(d.value(row, "detail") or "")))
    return out


def card(screen_id: str, now_s: float) -> dict | None:
    """This screen's fun-fact card now: A a Breakaway record, B a "Did you
    know?" sentence, both turning every CARD_PERIOD_S by the clock, card n
    opposite card n (DRAFT pairing). None when that deck is empty."""
    n = int(now_s // CARD_PERIOD_S)
    if screen_id.endswith("_b"):
        facts = fun_facts()
        if not facts:
            return None
        key, cat, text = facts[n % len(facts)]
        return {"kind": "fact", "category": cat, "text": text}
    recs = records()
    if not recs:
        return None
    rec, val, detail = recs[n % len(recs)]
    return {"kind": "record", "record": rec, "value": val, "detail": detail}
# Text for the facts screens rather than a table of its own.
FACTS_KEY = "fun_facts"


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    description: str = ""
    category: str = ""
    sort: int = 9999
    columns: tuple[str, ...] = ()
    rows: tuple[tuple, ...] = ()
    highlight_column: str = ""
    highlight_value: Any = None
    highlight_rows: frozenset = field(default_factory=frozenset)
    total_rows: int = 0
    truncated: bool = False

    def col(self, name: str) -> int | None:
        """A column's index, matching case-insensitively (`Team_key`)."""
        want = name.lower()
        for i, c in enumerate(self.columns):
            if c.lower() == want:
                return i
        return None

    def value(self, row: tuple, name: str) -> Any:
        i = self.col(name)
        return row[i] if i is not None and i < len(row) else None

    def is_highlight(self, index: int) -> bool:
        return index in self.highlight_rows

    @property
    def shown_of(self) -> str:
        """"Showing 25 of 1,186", or "" when everything is shown."""
        n = len(self.rows)
        return f"Showing {n:,} of {self.total_rows:,}" if self.total_rows > n else ""


def _parse(key: str, raw: dict) -> Dataset:
    hl = raw.get("highlight") or {}
    rows = tuple(tuple(r) for r in (raw.get("rows") or []) if isinstance(r, list))
    return Dataset(
        key=key, title=str(raw.get("title") or key.replace("_", " ").title()),
        description=str(raw.get("description") or ""),
        category=str(raw.get("category") or ""),
        sort=int(raw["sort"]) if isinstance(raw.get("sort"), (int, float)) else 9999,
        columns=tuple(str(c) for c in (raw.get("columns") or [])),
        rows=rows,
        highlight_column=str(hl.get("column") or ""),
        highlight_value=hl.get("value"),
        highlight_rows=frozenset(i for i in (hl.get("rows") or []) if isinstance(i, int)),
        total_rows=int(raw.get("total_rows") or len(rows)),
        truncated=bool(raw.get("truncated")),
    )


def all_datasets(enabled_only: bool = True) -> list[Dataset]:
    """Every dataset home sent, by `sort`; only the cleared ones by default."""
    try:
        from app.db import db
        found = db.fetchall("SELECT uid, data FROM home_dataset")
    except Exception:
        return []
    on = set(dataset_settings.load()["enabled"])
    out = []
    for r in found:
        if enabled_only and r["uid"] not in on:
            continue
        try:
            out.append(_parse(r["uid"], json.loads(r["data"])))
        except (ValueError, TypeError):
            continue          # a malformed row must never take a screen down
    return sorted(out, key=lambda d: (d.sort, d.key))


def get(key: str, enabled_only: bool = True) -> Dataset | None:
    return next((d for d in all_datasets(enabled_only) if d.key == key), None)


def generic(enabled_only: bool = True) -> list[Dataset]:
    """Cleared datasets without a screen of their own, for the Datasets set."""
    return [d for d in all_datasets(enabled_only)
            if d.key not in DEDICATED and d.key != FACTS_KEY]


def page_pair(now_s: float, period_s: int = 30) -> tuple[Dataset | None, Dataset | None]:
    """(A's, B's) generic dataset right now. Paged by the clock, so both
    screens (and the pit-network page, by the server's clock) turn together
    with nothing to keep in step."""
    found = generic()
    if not found:
        return None, None
    pairs = (len(found) + 1) // 2
    p = int(now_s // period_s) % pairs
    a = found[2 * p]
    b = found[2 * p + 1] if 2 * p + 1 < len(found) else None
    return a, b


def header(name: str) -> str:
    """`longest_win_streak` → "Longest win streak"; `Top_Quality` → "Top quality"."""
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:].lower() if words else ""


def team_number(value: Any) -> str:
    """'frc3937' → '3937'."""
    s = str(value or "")
    return s[3:] if s.lower().startswith("frc") else s


def cell(value: Any) -> str:
    """A value as the screen prints it."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:,.1f}" if value != int(value) else f"{int(value):,}"
    if isinstance(value, int):
        return str(value) if 1900 <= value <= 2100 else f"{value:,}"
    s = str(value)
    return team_number(s) if s.lower().startswith("frc") and s[3:].isdigit() else s


def visible_columns(d: Dataset) -> list[int]:
    """Column indexes to show: `team_key` hides when `team_number` is there."""
    hide = set()
    if d.col("team_number") is not None and d.col("team_key") is not None:
        hide.add(d.col("team_key"))
    return [i for i in range(len(d.columns)) if i not in hide]


def nickname(team: str) -> str:
    """A team's nickname, if home ever sent `tba_team` (it doesn't today)."""
    try:
        from app.db import db
        row = db.fetchone("SELECT nickname FROM tba_team WHERE team_number = ?", (team,))
        return row["nickname"] if row and row["nickname"] else ""
    except Exception:
        return ""


def fun_facts() -> list[tuple[str, str, str]]:
    """(key, category, text) from home's `fun_facts` dataset, by its `sort`.
    Only once an adult has cleared it, like every dataset."""
    d = get(FACTS_KEY)
    if d is None:
        return []
    out = []
    for row in d.rows:
        text = d.value(row, "fact_text")
        if not text:
            continue
        out.append((str(d.value(row, "fact_key") or ""), str(d.value(row, "category") or ""),
                    str(text).strip()))
    return out
