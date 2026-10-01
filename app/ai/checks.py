"""
"Every number is real", in code. The models choose and word; this decides.

**Findings** (`check_findings`) are judged against the **ledger**: every tool
call the analyst actually made, with the result it actually got.

* Each `evidence` item must name a call in the ledger (same tool, and every
  argument it gives equal to what the call echoed), and every number in its
  `value` must be in one of the finding's cited results.
* `metric.value` and `compare_to` are numbers, so they must **equal** a
  number in a cited result. Rounding 97.08 to 97.1 is a rejection, not a
  tolerance: the figure the board shows has to be one a tool returned.
* A count cited only from `faults` must be the count of the fault or device
  the finding names (`summary` / `by_device`), not merely a number somewhere
  in that result. Found in the first real trial: "Pigeon2: 4 warnings" passed
  on a 4 that belonged to another fault; the Pigeon has 5.
* Numbers written in prose (`claim`) may be **display-rounded** ("7.01 V" for
  7.0123), because sentences do that, but must come from a cited result.
* `severity: fault` needs a fault behind it: a cited `faults` result whose
  `summary` has a group marked `fault` (the pit board's first four), or a cited `session_overview` vital the tool itself marked
  `fault`. Status is the robot's own fault flags, never a model's opinion.

**Boards** (`check_board`) are judged against the findings that passed: the
designer saw only those, so any figure not in them was invented. Same rules
(exact in number fields, display rounding in strings), plus the red budget:
`fault` only when a finding is a fault, and at most one region of cards
(vitals, subsystems, faults) carrying it.

**Charts** (`check_charts`) carry no model-written figure at all: the
designer picks a kind and a source from `chart_options()` (the signals the
findings stand on) and the publisher fetches the data. The check is that the
source is one of those and that the fetch drew something.

A number counts as allowed context too when it is in the session's own name
or match key ("qm14"), or one of the three published figures the status rules
use (6.8 V, 20 ms, 70 %/100 % CAN).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.robot.diagnostics import BROWNOUT_V, CAN_FULL_PCT, LOOP_PERIOD_MS

PUBLISHED = (BROWNOUT_V, LOOP_PERIOD_MS, 70.0, CAN_FULL_PCT)
SEVERITY_ORDER = {"fault": 0, "warn": 1, "ok": 2, "idle": 3}

# A number standing on its own: not glued to a word before it ("qm14", "frc3937"),
# though a unit may follow ("12V", "40A").
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?")


# What a card's value may be: a figure, or a yes/no reading. Words and
# punctuation go in label, unit and detail (trial 4 wrote ":9").
_FIGURE = re.compile(r"^(?:[-+]?\d[\d,]*(?:\.\d+)?%?|yes|no|—)$", re.IGNORECASE)


@dataclass
class Call:
    tool: str
    args: dict
    result: dict


@dataclass
class Ledger:
    calls: list[Call] = field(default_factory=list)

    def add(self, tool: str, result: dict) -> None:
        self.calls.append(Call(tool, dict(result.get("args") or {}), result))

    def matching(self, tool: str, args: dict | None) -> list[Call]:
        def same(a: Any, b: Any) -> bool:
            return a == b or str(a) == str(b)

        given = {k: v for k, v in (args or {}).items() if v is not None}
        return [c for c in self.calls if c.tool == tool
                and all(k in c.args and same(c.args[k], v) for k, v in given.items())]


# ── numbers ──────────────────────────────────────────────────────────────

def numbers(obj: Any, skip_args: bool = True) -> list[float]:
    """Every number inside a JSON value (booleans aren't numbers)."""
    out: list[float] = []

    def walk(v: Any, key: str = "") -> None:
        if skip_args and key == "args":
            return
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.append(float(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(obj)
    return out


def text_numbers(text: str) -> list[tuple[float, int]]:
    """(value, decimal places) for each number written in `text`."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        s = m.group().rstrip(",").replace(",", "")
        try:
            out.append((float(s), len(s.partition(".")[2])))
        except ValueError:
            pass
    return out


def equal(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(b))


def in_set(value: float, pool: list[float]) -> bool:
    return any(equal(value, x) for x in pool)


def shown_as(value: float, places: int, pool: list[float]) -> bool:
    """
    Is `value` (written with `places` decimals) some pool number, displayed?

    Within half a unit of the last place, so 11.25 may read "11.3" or "11.2":
    Python's round() goes to even and a person rounds half up; both are honest.
    """
    tol = 0.5 * 10 ** -places + 1e-9
    return any(abs(value - y) <= tol for x in pool for y in (x, abs(x)))


def context_numbers(*texts: str) -> list[float]:
    return [v for t in texts for v, _ in text_numbers(t or "")]


# ── findings ─────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _named_fault_status(f: dict, cited: list[Call]) -> bool | None:
    """
    When a finding names latched faults from a cited `faults` summary: True if
    any it names is red on the pit board, False if none are, None if it names
    none. Trial 4: "StickyFaultField" (warn) passed as a fault because a
    different, red fault was in the same result.
    """
    words = _norm(" ".join([(f.get("metric") or {}).get("name", ""), f.get("claim", ""),
                            f.get("id", "")]))
    named = [g for c in cited if c.tool == "faults" for g in c.result.get("summary") or []
             if _norm(g["fault"]) in words]
    if not named:
        return None
    return any(g.get("status") == "fault" for g in named)


def _has_fault(call: Call) -> bool:
    r = call.result
    if call.tool == "faults":
        return any(g.get("status") == "fault" for g in r.get("summary") or [])
    if call.tool == "session_overview":
        return any(v.get("status") == "fault" for v in r.get("vitals") or [])
    return False


def check_findings(insights: dict, ledger: Ledger, session_uids: list[str],
                   context: list[float]) -> list[str]:
    """Problems with the analyst's findings. Empty means they may go on."""
    problems: list[str] = []
    if sorted(insights.get("session_uids") or []) != sorted(session_uids):
        problems.append(f"session_uids must be {session_uids}")
    allowed_context = list(context) + list(PUBLISHED)

    for i, f in enumerate(insights.get("findings") or []):
        at = f"finding {i + 1} ({f.get('id', '?')})"
        cited: list[Call] = []
        matched: list[tuple[int, dict]] = []
        for j, ev in enumerate(f.get("evidence") or []):
            calls = ledger.matching(ev.get("tool", ""), ev.get("args"))
            if not calls:
                problems.append(f"{at}: evidence {j + 1} cites {ev.get('tool')!r} with "
                                f"{ev.get('args')}, a call that was never made")
                continue
            cited += calls
            matched.append((j, ev))
        # An evidence value must be in one of the finding's cited results, not
        # necessarily its own item's (trials 5-6: a fault citation carried the
        # overview's count, also cited). Every figure is still a cited one.
        cited_pool = [n for c in cited for n in numbers(c.result)]
        for j, ev in matched:
            for n in numbers(ev.get("value"), skip_args=False):
                if not in_set(n, cited_pool):
                    # Say where it *is*, if anywhere: a hint at which call to
                    # cite, never a change to the figure.
                    elsewhere = sorted({c.tool for c in ledger.calls
                                        if in_set(n, numbers(c.result))})
                    hint = (f" (it is in the {' / '.join(elsewhere)} result: cite that call)"
                            if elsewhere else " (no tool returned it)")
                    problems.append(f"{at}: evidence {j + 1} value {n:g} isn't in any "
                                    f"result it cites{hint}")
        if not cited:
            continue

        pool = [n for c in cited for n in numbers(c.result)]
        metric = f.get("metric") or {}
        for key in ("value", "compare_to"):
            v = metric.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and not in_set(v, pool):
                problems.append(f"{at}: metric {key} {v:g} is not in its evidence "
                                f"(a tool must have returned exactly this number)")

        # A count read from `faults` must be the count of what the finding names:
        # "4" is in that result many times over, so presence alone proves nothing.
        v = metric.get("value")
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and cited and all(c.tool == "faults" for c in cited)):
            words = _norm(" ".join([metric.get("name", ""), f.get("claim", "")]))
            # A finding naming a fault counts that fault's devices, even when it
            # also lists the devices (trial 3: "StatorCurrLimit: 7" took a motor's
            # own 7 faults; the fault is on 8). Per-device counts only otherwise.
            named = [g["n_devices"] for c in cited for g in c.result.get("summary") or []
                     if _norm(g["fault"]) in words]
            if not named:
                named = [d["n_faults"] for c in cited for d in c.result.get("by_device") or []
                         if _norm(d["device"]) in words]
            if not any(equal(float(v), float(n)) for n in named):
                problems.append(f"{at}: {v:g} isn't the count for anything this finding "
                                f"names (faults gives summary[].n_devices per fault and "
                                f"by_device[].n_faults per device)")

        prose_pool = pool + [n for c in cited for n in numbers(c.args, skip_args=False)]
        prose_pool += allowed_context
        for v, places in text_numbers(f.get("claim", "")):
            if not shown_as(v, places, prose_pool):
                problems.append(f"{at}: claim says {v:g}, which no cited tool returned")

        named_red = _named_fault_status(f, cited)
        if f.get("severity") == "fault" and named_red is False:
            problems.append(f"{at}: severity 'fault' for a fault the pit board ranks warn "
                            f"(its faults summary status); use warn")
        elif f.get("severity") == "fault" and not any(_has_fault(c) for c in cited):
            red = sorted({g["fault"] for c in ledger.calls if c.tool == "faults"
                          for g in c.result.get("summary") or [] if g.get("status") == "fault"})
            problems.append(
                f"{at}: severity 'fault' needs a latched fault in its evidence: add "
                f"{{\"tool\": \"faults\", \"args\": {{\"session_uid\": ...}}}} to its "
                f"evidence" + (f" (its summary marks {', '.join(red)} as fault)" if red else "")
                + ", or make it warn")
    return problems


_STAT = {"v_min": "min", "v_max": "max", "v_avg": "mean"}


def chart_options(insights: dict, ledger: Ledger | None = None,
                  extreme: Callable[[str, str, str], dict | None] | None = None
                  ) -> list[dict]:
    """
    What the designer may graph: the signals the analyst's findings stand on.

    * a finding's `series` → a line, and bars of that signal across devices;
    * a cited `series_stats` / `series_1s` → bars across devices;
    * a cited `compare_sessions` → sessions;
    * a cited `session_overview` figure (matched by value) → bars of the
      signal it was read from, and, through `extreme(session_uid, signal,
      column)`, a line on the device that set the figure. Code picks that
      device from the data; the designer only chooses whether to draw it.
    """
    out, seen = [], set()

    def add(kind: str, source: dict, finding: str) -> None:
        key = (kind, json.dumps(source, sort_keys=True))
        if key not in seen:
            seen.add(key)
            out.append({"kind": kind, "finding": finding, "source": source})

    for f in insights.get("findings") or []:
        fid = f.get("id", "")
        s = f.get("series")
        if isinstance(s, dict):
            add("line", {k: s.get(k) for k in ("session_uid", "device_type", "can_id",
                                                 "signal")}, fid)
            add("bars", {"session_uid": s.get("session_uid"), "signal": s.get("signal")}, fid)
        for ev in f.get("evidence") or []:
            a = ev.get("args") or {}
            if ev.get("tool") == "session_overview" and ledger is not None:
                value = (f.get("metric") or {}).get("value")
                for call in ledger.matching("session_overview", a):
                    for v in call.result.get("vitals") or []:
                        sig, col = v.get("signal"), v.get("column") or "v_max"
                        if (not sig or isinstance(v.get("value"), bool)
                                or not isinstance(value, (int, float))
                                or not equal(float(value), float(v["value"]))):
                            continue
                        uid = call.args.get("session_uid")
                        add("bars", {"session_uid": uid, "signal": sig,
                                     "column": _STAT.get(col, "max")}, fid)
                        dev = extreme(uid, sig, col) if extreme else None
                        if dev:
                            add("line", {"session_uid": uid, "device_type": dev["device_type"],
                                         "can_id": dev["can_id"], "signal": sig,
                                         "column": col}, fid)
            if ev.get("tool") in ("series_stats", "series_1s") and a.get("signal"):
                add("bars", {"session_uid": a.get("session_uid"), "signal": a["signal"]}, fid)
            if ev.get("tool") == "compare_sessions" and a.get("signal"):
                add("sessions", {"session_uids": a.get("session_uids") or [],
                                 "signal": a["signal"]}, fid)
    return out


def check_charts(board: dict, options: list[dict], session_uids: list[str]) -> list[str]:
    """A chart may only graph what the findings stand on (`options`), and must draw."""
    problems = []
    signals = {(o["source"].get("session_uid"), o["source"]["signal"])
               for o in options if o["kind"] != "sessions"}
    compared = [o["source"] for o in options if o["kind"] == "sessions"]
    for i, c in enumerate(board.get("charts") or []):
        at, src, kind = f"charts[{i}]", c.get("source") or {}, c.get("kind")
        col = src.get("column")
        if kind in ("line", "bars"):
            if src.get("session_uid") not in session_uids:
                problems.append(f"{at}: session_uid must be one of {session_uids}")
            elif (src.get("session_uid"), src.get("signal")) not in signals:
                problems.append(f"{at}: {src.get('signal')!r} isn't a signal the findings "
                                f"stand on; pick from the chart options")
        if kind == "line" and (src.get("device_type") is None or src.get("can_id") is None):
            problems.append(f"{at}: a line needs device_type and can_id")
        if kind == "line" and col not in (None, "v_min", "v_max", "v_avg"):
            problems.append(f"{at}: a line's column is v_min, v_max or v_avg")
        if kind == "bars" and col not in (None, "min", "max", "mean", "last"):
            problems.append(f"{at}: bars' column is min, max, mean or last")
        if kind == "sessions" and not any(
                o["signal"] == src.get("signal")
                and sorted(o["session_uids"]) == sorted(src.get("session_uids") or [])
                for o in compared):
            problems.append(f"{at}: sessions charts need a compare_sessions call in the "
                            f"findings with the same sessions and signal")
        data = c.get("data") or {}
        if not problems and not (data.get("points") or data.get("bars") or data.get("sessions")):
            problems.append(f"{at}: no data for {src.get('signal')!r} "
                            f"({src.get('device_type')} {src.get('can_id')})")
    return problems


def series_refs(insights: dict) -> list[dict]:
    keys = ("session_uid", "device_type", "can_id", "signal")
    out = []
    for f in insights.get("findings") or []:
        s = f.get("series")
        if isinstance(s, dict):
            out.append({k: s.get(k) for k in keys})
    return out


# ── boards ───────────────────────────────────────────────────────────────

def _strings(board: dict) -> list[tuple[str, str]]:
    out = [("title", board.get("title", ""))]
    h = board.get("headline") or {}
    out += [("headline.title", h.get("title", "")), ("headline.sentence", h.get("sentence", ""))]
    for region in ("vitals", "faults"):
        for i, r in enumerate(board.get(region) or []):
            for k in ("label", "value", "unit", "detail"):
                out.append((f"{region}[{i}].{k}", r.get(k) or ""))
    for i, s in enumerate(board.get("subsystems") or []):
        out += [(f"subsystems[{i}].name", s.get("name", "")),
                (f"subsystems[{i}].note", s.get("note") or "")]
    for i, c in enumerate(board.get("charts") or []):
        out += [(f"charts[{i}].title", c.get("title", "")),
                (f"charts[{i}].unit", c.get("unit") or "")]
    foot = board.get("footer") or {}
    out += [("footer.left", foot.get("left", "")), ("footer.right", foot.get("right", ""))]
    return out


def check_board(board: dict, insights: dict, context: list[float]) -> list[str]:
    """Problems with the designer's board, judged against the passed findings."""
    problems: list[str] = []
    pool = numbers(insights, skip_args=False)
    for f in insights.get("findings") or []:
        pool += [v for v, _ in text_numbers(f.get("claim", ""))]
    pool += list(context) + list(PUBLISHED)

    for region in ("vitals", "faults"):
        for i, r in enumerate(board.get(region) or []):
            if not _FIGURE.match(str(r.get("value", "")).strip()):
                problems.append(f"{region}[{i}].value {r.get('value')!r} must be just the "
                                f"figure (like '8' or '11.25'), its unit in unit")
    for where, text in _strings(board):
        for v, places in text_numbers(text):
            if not shown_as(v, places, pool):
                problems.append(f"{where}: {v:g} isn't in the findings")
    for i, s in enumerate(board.get("subsystems") or []):
        for k in ("stator_a", "supply_a", "pdh_a", "temp_f"):
            v = s.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and not in_set(v, pool):
                problems.append(f"subsystems[{i}].{k}: {v:g} isn't in the findings")

    severities = [f.get("severity") for f in insights.get("findings") or []]
    worst = min(severities, key=lambda s: SEVERITY_ORDER.get(s, 9), default="idle")
    head = (board.get("headline") or {}).get("status")
    if head != worst:
        problems.append(f"headline.status is {head!r}; the worst finding is {worst!r}")
    red = [region for region in ("vitals", "subsystems", "faults")
           if any(x.get("status") == "fault" for x in board.get(region) or [])]
    if (red or head == "fault") and "fault" not in severities:
        problems.append("'fault' on the board, but no finding is a fault")
    if len(red) > 1:
        problems.append(f"red in {len(red)} regions ({', '.join(red)}); one at most")

    allowed = series_refs(insights)
    keys = ("session_uid", "device_type", "can_id", "signal")
    for region in ("vitals", "faults"):
        for i, r in enumerate(board.get(region) or []):
            s = r.get("series")
            if isinstance(s, dict) and {k: s.get(k) for k in keys} not in allowed:
                problems.append(f"{region}[{i}].series isn't a series from the findings")
    return problems
