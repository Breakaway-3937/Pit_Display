"""
Breakaway's matches today, with results and our record: B's half of the
"Next match" set (`app/overhead.py`; A shows the Nexus queue).

**Only our matches** (Brayden, 2026-10-02: "I need only the matches for
Breakaway on the day's match schedule"). Results will come from the VM's send
(home/REQUESTS.md R12).

No Qt: the native face (`schedule_overlay.py`) and the pit-network page
(`webcast/state.py`) read the same rows.

* **The schedule is Nexus's** (`nexus.status.matches`): every match, in play
  order, with its time (the estimate, which becomes the actual once it happens).
* **Results and our record are TBA's**, from home's `tba_match` rows for this
  event (home/REQUESTS.md R5/R10). Nexus carries no scores. With no rows yet,
  the schedule shows without results and the record is left out, never
  guessed.
* **Nothing is computed beyond counting.** A row's result is TBA's
  `red_score`, `blue_score`, `winning_alliance`; our record counts our rows
  that have a winner (or a tie).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any


def tba_suffix(label: str) -> str | None:
    """Nexus's label → TBA's match-key suffix: 'Qualification 14' → 'qm14',
    'Playoff 3' → 'sf3m1' (double elimination), 'Final 2' → 'f1m2'.
    Practice matches have no TBA key."""
    m = re.match(r"^\s*(Qualification|Playoff|Final)\s+(\d+)\s*$", label or "")
    if not m:
        return None
    kind, n = m.group(1), int(m.group(2))
    return {"Qualification": f"qm{n}", "Playoff": f"sf{n}m1", "Final": f"f1m{n}"}[kind]


def short(label: str) -> str:
    return (label or "").replace("Qualification", "Qual")


# The VM's send (home/REQUESTS.md R12): Breakaway's matches today as a
# home dataset, columns match_key (TBA, '2026arli_qm14'), red_score,
# blue_score, winning_alliance ('' = tie). Read first; `tba_match` is the
# fallback should home ever send that table.
RESULTS_DATASET = "bk_matches_today"


def _dataset_results(event_key: str) -> dict[str, dict[str, Any]]:
    from app import datasets
    d = datasets.get(RESULTS_DATASET, enabled_only=False)
    if d is None:
        return {}
    out = {}
    for row in d.rows:
        key = str(d.value(row, "match_key") or "")
        ev, _, suffix = key.partition("_")
        if not suffix or (event_key and ev != event_key):
            continue
        red, blue = d.value(row, "red_score"), d.value(row, "blue_score")
        if red is None and blue is None:
            continue
        out[suffix] = {"red_score": red, "blue_score": blue,
                       "winning_alliance": d.value(row, "winning_alliance") or ""}
    return out


def _results(event_key: str) -> dict[str, dict[str, Any]]:
    """Results for this event, by match-key suffix: the VM's dataset first,
    then `tba_match`. {} when neither has arrived."""
    found = _dataset_results(event_key)
    if found:
        return found
    if not event_key:
        return {}
    try:
        from app.db import db
        rows = db.fetchall("SELECT uid, data FROM tba_match WHERE event_key = ?",
                           (event_key,))
    except Exception:
        return {}
    out = {}
    for r in rows:
        try:
            data = json.loads(r["data"])
        except ValueError:
            continue
        if data.get("red_score") is None and data.get("blue_score") is None:
            continue
        out[r["uid"].partition("_")[2]] = data
    return out


def build(team: str, now_ms: int | None = None) -> dict[str, Any]:
    """Every match at the event, ours marked, with results and our record."""
    try:
        from app.nexus import nexus
        status = nexus.status
        event_key = nexus.event_key
        nxt = nexus.next_match()
    except Exception:
        return {"rows": [], "record": None, "has_results": False}
    if status is None:
        return {"rows": [], "record": None, "has_results": False}
    now_ms = now_ms or int(time.time() * 1000)
    results = _results(event_key)
    # Played is a matter of play order, not the clock: everything before the
    # match on the field (the last one Nexus has `On field`) is done, and with
    # nothing on the field, everything before the first match with a live
    # status (queuing, on deck). The clock is only the last resort.
    from app.nexus.api import MatchState
    matches = status.matches
    on_field = [i for i, m in enumerate(matches) if m.status == MatchState.ON_FIELD]
    live = [i for i, m in enumerate(matches) if m.status in MatchState.ORDER]
    pivot = on_field[-1] if on_field else (live[0] if live else None)
    rows = []
    wins = losses = ties = 0
    for i, m in enumerate(matches):
        t = m.times
        at = t.estimated_start or t.estimated_queue or t.scheduled_start
        ours = m.alliance_of(team) or ""
        if not ours:
            continue                       # only Breakaway's matches
        res = results.get(tba_suffix(m.label) or "")
        result = None
        outcome = ""
        if res is not None:
            winner = res.get("winning_alliance") or ""
            result = {"red": res.get("red_score"), "blue": res.get("blue_score"),
                      "winner": winner}
            if ours:
                if winner == ours:
                    outcome, wins = "W", wins + 1
                elif winner in ("red", "blue"):
                    outcome, losses = "L", losses + 1
                elif winner == "" and res.get("red_score") is not None:
                    outcome, ties = "T", ties + 1
        is_current = bool(on_field) and i == on_field[-1]
        if pivot is not None:
            played = result is not None or i < pivot
        else:
            played = result is not None or (at is not None and at < now_ms - 3 * 60_000)
        rows.append({
            "label": m.label, "short": short(m.label), "at_ms": at,
            "red": [x for x in (m.red_teams or []) if x],
            "blue": [x for x in (m.blue_teams or []) if x],
            "ours": ours, "played": played, "current": is_current,
            "next": nxt is not None and m.label == nxt.label and bool(ours),
            "result": result, "outcome": outcome,
        })
    has_results = bool(results)
    return {"rows": rows, "has_results": has_results,
            "record": (wins, losses, ties) if has_results and (wins or losses or ties) else None}


def window_start(rows: list[dict], keep_before: int = 2) -> int:
    """Where the visible window starts: a couple of matches before the first
    one still to be played, so the screen is about what's next."""
    first = next((i for i, r in enumerate(rows) if not r["played"]), len(rows))
    return max(0, first - keep_before)
