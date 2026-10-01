"""
The read-only tools a model may call, over this machine's log tables: the
home MCP server's nine, and `match_context` (TBA data, `home/REQUESTS.md` R5).

**The contract is the home MCP server's** (`pit/mcp_server.py` in the home
repo, `home/HANDOFF.md` M5): same tool names, same arguments, same result
keys, so prompts and checks written against one work against the other. Every
result echoes the arguments it ran with as `args`; an unknown session is
`{"args": …, "error": …}`, never an exception. Lists are capped at `MAX_ROWS`
with `truncated: true`. Floats are rounded to 4 places, which is the figure a
model copies and the checker compares against.

Figures are chosen as `app/robot/diagnostics.py` chooses them (the pit board
and these tools must never disagree) but returned as numbers, not formatted
strings, with `detail` as a dict of the supporting figures.

There is no free-form SQL tool, on purpose: for a local model, the tools are
the safety rail. Only SELECTs on `series`, `signal`, `device`, `fault_event`,
`log_session` and `samples.sample_1s`.
"""

from __future__ import annotations

import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.db import db
from app.robot.diagnostics import BROWNOUT_V, FAULT_PRIORITY, LOOP_PERIOD_MS

MAX_ROWS = 200      # an LLM's context is the budget
FAULT_RANKS = 4     # the first four of FAULT_PRIORITY are red on the pit board

_APP_SUFFIXES = {
    " Stator Motor Current": "stator_a",
    " Supply Motor Current": "supply_a",
    " PDH Current": "pdh_a",
    " Temp F": "temp_f",
}


# ── which connection ─────────────────────────────────────────────────────

# The app's own connection by default. A run off the GUI thread brings its
# own (`local.ThreadDB`) for that thread, so a long analysis never shares a
# connection, or a transaction, with the panels.
_current: ContextVar = ContextVar("ai_db", default=None)


def connection():
    return _current.get() or db


@contextmanager
def using(handle):
    """Run the tools (and the local sink) on `handle` within this block."""
    token = _current.set(handle)
    try:
        yield handle
    finally:
        _current.reset(token)


# ── helpers ──────────────────────────────────────────────────────────────

def _clean(v: Any) -> Any:
    if isinstance(v, float):
        return None if not math.isfinite(v) else round(v, 4)
    return v


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    return [{k: _clean(r[k]) for k in r.keys()} for r in connection().fetchall(sql, params)]


def _session_id(uid: str) -> int | None:
    row = connection().fetchone("SELECT id FROM log_session WHERE uid = ?", (uid,))
    return row["id"] if row else None


def _missing(args: dict) -> dict:
    return {"args": args, "error": f"no session {args.get('session_uid')!r}; use list_sessions"}


def _cap(rows: list) -> tuple[list, bool]:
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def _num(sid: int, name: str, col: str = "v_max") -> float | None:
    agg = "MIN" if col == "v_min" else "MAX"
    col = {"v_min": "v_min", "v_max": "v_max", "v_mean": "v_mean", "v_last": "v_last"}[col]
    row = connection().fetchone(f"""SELECT {agg}(se.{col}) x FROM series se
                          JOIN signal s ON s.id = se.signal_id
                          WHERE se.session_id = ? AND s.name = ?""", (sid, name))
    return _clean(row["x"]) if row and row["x"] is not None else None


def _mean(sid: int, name: str) -> float | None:
    row = connection().fetchone("""SELECT AVG(se.v_mean) x FROM series se
                         JOIN signal s ON s.id = se.signal_id
                         WHERE se.session_id = ? AND s.name = ?""", (sid, name))
    return _clean(row["x"]) if row and row["x"] is not None else None


# ── the tools ────────────────────────────────────────────────────────────

def list_sessions(match_key: str | None = None, since: str | None = None,
                  limit: int = 20) -> dict[str, Any]:
    args = {"match_key": match_key, "since": since, "limit": limit}
    limit = max(1, min(int(limit), 100))
    rows = _rows(
        """SELECT ls.uid, ls.source_name AS name, ls.match_key, ls.started_at, ls.duration_s,
                  NULL AS origin,
                  (SELECT COUNT(*) FROM series se WHERE se.session_id = ls.id) AS n_series,
                  (SELECT COALESCE(SUM(se.n_stored), 0) FROM series se
                    WHERE se.session_id = ls.id) AS n_samples,
                  0 AS deleted
           FROM log_session ls
           WHERE ls.uid IS NOT NULL AND (? IS NULL OR ls.match_key = ?)
             AND (? IS NULL OR ls.imported_at >= ?)
           ORDER BY ls.imported_at DESC, ls.id DESC LIMIT ?""",
        (match_key, match_key, since, since, limit))
    return {"args": args, "sessions": rows}


def session_overview(session_uid: str) -> dict[str, Any]:
    args = {"session_uid": session_uid}
    s = _session_id(session_uid)
    if s is None:
        return _missing(args)
    vit: list[dict] = []

    # `signal` / `column`: the series the figure was read from, so a chart of
    # it can be drawn (pipeline chart options); None for a derived figure.
    def add(label, value, unit, status, detail=None, signal=None, column=None):
        vit.append({"label": label, "value": value, "unit": unit, "status": status,
                    "detail": detail, "signal": signal, "column": column})

    batt = "SupplyVoltage"
    sag = _num(s, batt, "v_min")
    if sag is None:
        batt = "SystemStats/BatteryVoltage"
        sag = _num(s, batt, "v_min")
    rest = _num(s, "SupplyVoltage") or _num(s, "SystemStats/BatteryVoltage")
    if sag is not None:
        status = "fault" if sag <= BROWNOUT_V else "warn" if sag <= BROWNOUT_V + 1.0 else "ok"
        add("Battery sag", sag, "V", status, {"rest_v": rest, "brownout_v": BROWNOUT_V},
            batt, "v_min")
    brown = _num(s, "SystemStats/BrownedOut")
    if brown is not None:
        add("Browned out", bool(brown > 0), "", "fault" if brown > 0 else "ok",
            signal="SystemStats/BrownedOut", column="v_max")
    if (peak := _num(s, "StatorCurrent")) is not None:
        add("Peak motor current", peak, "A", "idle", signal="StatorCurrent", column="v_max")
    total_sig = "RealOutputs/Robot Total Current Amps"
    total = _num(s, total_sig)
    if total is None:
        total_sig = "PowerDistribution/TotalCurrent"
        total = _num(s, total_sig)
    if total is not None:
        add("Peak total draw", total, "A", "idle", signal=total_sig, column="v_max")
    if (watts := _num(s, "RealOutputs/Robot Total Power Watts")) is not None:
        add("Peak power", round(watts / 1000, 4), "kW", "idle",
            signal="RealOutputs/Robot Total Power Watts", column="v_max")
    if (hot := _num(s, "DeviceTemp")) is not None:
        add("Hottest motor", hot, "°C", "idle", signal="DeviceTemp", column="v_max")
    can = _mean(s, "SystemStats/CANBus/Utilization")
    can_hi = _num(s, "SystemStats/CANBus/Utilization")
    if can is not None:
        scale = 100 if (can_hi or can) <= 1.0 else 1
        pct = round(can * scale, 4)
        add("CAN bus", pct, "%", "warn" if pct >= 70 else "ok",
            {"peak_pct": round((can_hi or can) * scale, 4)},
            "SystemStats/CANBus/Utilization", "v_avg")
    errs = sum(_num(s, n) or 0.0 for n in ("SystemStats/CANBus/ReceiveErrorCount",
                                           "SystemStats/CANBus/TransmitErrorCount",
                                           "SystemStats/CANBus/OffCount"))
    if errs:
        add("CAN errors", errs, "", "warn")
    loop = _mean(s, "RealOutputs/LoggedRobot/FullCycleMS")
    if loop is not None:
        add("Loop time", loop, "ms", "warn" if loop > LOOP_PERIOD_MS else "ok",
            {"worst_ms": _num(s, "RealOutputs/LoggedRobot/FullCycleMS"),
             "budget_ms": LOOP_PERIOD_MS}, "RealOutputs/LoggedRobot/FullCycleMS", "v_avg")
    if (cpu := _num(s, "SystemStats/CPUTempCelsius")) is not None:
        add("roboRIO CPU", cpu, "°C", "idle", signal="SystemStats/CPUTempCelsius",
            column="v_max")

    meta = _rows("""SELECT source_name AS name, duration_s, match_key, started_at
                    FROM log_session WHERE id = ?""", (s,))[0]
    n = connection().fetchone("SELECT COUNT(*) n FROM fault_event WHERE session_id = ? AND sticky = 1",
                    (s,))["n"]
    return {"args": args, "session": meta, "vitals": vit, "sticky_fault_count": n}


def list_signals(session_uid: str, contains: str | None = None) -> dict[str, Any]:
    args = {"session_uid": session_uid, "contains": contains}
    s = _session_id(session_uid)
    if s is None:
        return _missing(args)
    rows = _rows(
        """SELECT d.device_type, d.can_id, d.label AS device_label, sg.name AS signal,
                  sg.unit, se.n_stored
           FROM series se JOIN signal sg ON sg.id = se.signal_id
           JOIN device d ON d.id = se.device_id
           WHERE se.session_id = ? AND (? IS NULL OR sg.name LIKE '%' || ? || '%')
           ORDER BY sg.name, d.device_type, d.can_id""", (s, contains, contains))
    rows, truncated = _cap(rows)
    return {"args": args, "signals": rows, "truncated": truncated}


def series_stats(session_uid: str, signal: str, device_type: str | None = None,
                 can_id: int | None = None) -> dict[str, Any]:
    args = {"session_uid": session_uid, "signal": signal, "device_type": device_type,
            "can_id": can_id}
    s = _session_id(session_uid)
    if s is None:
        return _missing(args)
    rows = _rows(
        """SELECT d.device_type, d.can_id, d.label AS device_label,
                  se.v_min AS min, se.v_max AS max, se.v_mean AS mean, se.v_last AS last,
                  se.n_stored
           FROM series se JOIN signal sg ON sg.id = se.signal_id
           JOIN device d ON d.id = se.device_id
           WHERE se.session_id = ? AND sg.name = ?
             AND (? IS NULL OR d.device_type = ?) AND (? IS NULL OR d.can_id = ?)
           ORDER BY d.device_type, d.can_id""",
        (s, signal, device_type, device_type, can_id, can_id))
    rows, truncated = _cap(rows)
    return {"args": args, "devices": rows, "truncated": truncated}


def series_1s(session_uid: str, signal: str, device_type: str, can_id: int,
              column: str = "v_max", max_points: int = 120) -> dict[str, Any]:
    args = {"session_uid": session_uid, "signal": signal, "device_type": device_type,
            "can_id": can_id, "column": column, "max_points": max_points}
    if column not in ("v_min", "v_max", "v_avg"):
        return {"args": args, "error": "column must be v_min, v_max or v_avg"}
    s = _session_id(session_uid)
    if s is None:
        return _missing(args)
    max_points = max(2, min(int(max_points), 500))
    rows = _rows(
        f"""SELECT r.t_s, r.{column} AS v FROM samples.sample_1s r
            JOIN series se ON se.id = r.series_id
            JOIN signal sg ON sg.id = se.signal_id
            JOIN device d ON d.id = se.device_id
            WHERE se.session_id = ? AND sg.name = ? AND d.device_type = ? AND d.can_id = ?
            ORDER BY r.t_s""", (s, signal, device_type, can_id))
    pts = [[r["t_s"], r["v"]] for r in rows if r["v"] is not None]
    if len(pts) > max_points:   # even decimation keeps the dip that matters
        step = (len(pts) - 1) / (max_points - 1)
        pts = [pts[int(round(i * step))] for i in range(max_points)]
    return {"args": args, "points": pts, "n_seconds": len(rows)}


def faults(session_uid: str) -> dict[str, Any]:
    args = {"session_uid": session_uid}
    s = _session_id(session_uid)
    if s is None:
        return _missing(args)
    rows = _rows(
        """SELECT d.device_type, d.can_id, d.label AS device_label, sg.name AS signal,
                  f.sticky, f.t_ms_start, f.t_ms_end
           FROM fault_event f JOIN signal sg ON sg.id = f.signal_id
           JOIN device d ON d.id = f.device_id
           WHERE f.session_id = ?""", (s,))

    def rank(r):
        n = r["signal"]
        return (0 if r["sticky"] else 1,
                FAULT_PRIORITY.index(n) if n in FAULT_PRIORITY else 99, r["t_ms_start"])

    rows = sorted(rows, key=rank)
    # Grouped as the pit board groups them (diagnostics.faults()): a model
    # that has to count rows itself is computing a figure, and gets rejected.
    groups: dict[str, dict] = {}
    for r in rows:
        if not r["sticky"]:
            continue
        g = groups.setdefault(r["signal"], {
            "fault": r["signal"].replace("StickyFault_", ""),
            "status": "fault" if r["signal"] in FAULT_PRIORITY[:FAULT_RANKS] else "warn",
            "n_devices": 0, "devices": []})
        g["n_devices"] += 1
        g["devices"].append(r["device_label"] or f"{r['device_type']} {r['can_id']}")
    per_device: dict[str, dict] = {}
    for r in rows:
        if r["sticky"]:
            name = r["device_label"] or f"{r['device_type']} {r['can_id']}"
            d = per_device.setdefault(name, {"device": name, "device_type": r["device_type"],
                                             "can_id": r["can_id"], "n_faults": 0,
                                             "faults": []})
            d["n_faults"] += 1
            d["faults"].append(r["signal"].replace("StickyFault_", ""))
    rows, truncated = _cap(rows)
    return {"args": args, "summary": list(groups.values()),
            "by_device": sorted(per_device.values(), key=lambda d: -d["n_faults"]),
            "faults": rows, "truncated": truncated}


def subsystems(session_uid: str) -> dict[str, Any]:
    args = {"session_uid": session_uid}
    s = _session_id(session_uid)
    if s is None:
        return _missing(args)
    found: dict[str, dict] = {}
    for r in _rows("""SELECT sg.name, MAX(se.v_max) AS v_max FROM series se
                      JOIN signal sg ON sg.id = se.signal_id
                      WHERE se.session_id = ? AND sg.name LIKE 'RealOutputs/%'
                      GROUP BY sg.name""", (s,)):
        name = r["name"][len("RealOutputs/"):]
        for suffix, field in _APP_SUFFIXES.items():
            if name.endswith(suffix):
                sub = name[: -len(suffix)].strip()
                if sub and "/" not in sub:
                    found.setdefault(sub, {"subsystem": sub, "stator_a": None, "supply_a": None,
                                           "pdh_a": None, "temp_f": None, "status": "idle",
                                           "note": None})[field] = r["v_max"]
                break
    flagged: dict[str, str] = {}
    for r in connection().fetchall(
            """SELECT DISTINCT d.subsystem, sg.name FROM fault_event f
               JOIN device d ON d.id = f.device_id JOIN signal sg ON sg.id = f.signal_id
               WHERE f.session_id = ? AND f.sticky = 1
                 AND d.subsystem IS NOT NULL AND d.subsystem != ''""", (s,)):
        flagged.setdefault(r["subsystem"], r["name"].replace("StickyFault_", ""))
    for sub in found.values():
        if sub["subsystem"] in flagged:
            sub["status"], sub["note"] = "warn", flagged[sub["subsystem"]]
        elif sub["stator_a"] is not None or sub["temp_f"] is not None:
            sub["status"] = "ok"
    return {"args": args, "subsystems": sorted(found.values(), key=lambda x: x["subsystem"])}


def compare_sessions(session_uids: list[str], signal: str, device_type: str | None = None,
                     can_id: int | None = None) -> dict[str, Any]:
    args = {"session_uids": session_uids, "signal": signal, "device_type": device_type,
            "can_id": can_id}
    out = []
    for uid in list(session_uids)[:10]:
        rows = series_stats(uid, signal, device_type, can_id).get("devices", [])
        out.append({"session_uid": uid, "devices": rows[:20],
                    "min": min((r["min"] for r in rows if r["min"] is not None), default=None),
                    "max": max((r["max"] for r in rows if r["max"] is not None), default=None)})
    return {"args": args, "sessions": out, "truncated": len(session_uids) > 10}


def _our_team() -> str:
    try:
        from app.config import config
        return str(config.active_team.number)
    except Exception:                 # no config (the --mcp server): the team
        return "3937"


def _event_key() -> str:
    try:
        from app.nexus import settings as nexus_settings
        return nexus_settings.load().get("event_key") or ""
    except Exception:
        return ""


MATCH_WINDOW_MS = 15 * 60 * 1000      # a log within 15 min of a match's start is that match


def match_context(session_uid: str) -> dict[str, Any]:
    args = {"session_uid": session_uid}
    s = _session_id(session_uid)
    if s is None:
        return _missing(args)
    meta = connection().fetchone("SELECT match_key, started_at FROM log_session WHERE id = ?",
                                 (s,))
    team = _our_team()
    event = _event_key()
    row, how = None, None
    if meta["match_key"]:
        row = connection().fetchone(
            """SELECT uid, data FROM tba_match WHERE match_key = ?
               ORDER BY (event_key = ?) DESC, actual_ms DESC LIMIT 1""",
            (meta["match_key"].strip().lower(), event))
        how = "tagged" if row else None
    if row is None and meta["started_at"]:
        from datetime import datetime
        try:
            # A log's start is the machine's local time (parsed from its name).
            start_ms = int(datetime.fromisoformat(meta["started_at"]).timestamp() * 1000)
        except ValueError:
            start_ms = None
        if start_ms is not None:
            row = connection().fetchone(
                """SELECT uid, data FROM tba_match WHERE actual_ms IS NOT NULL
                     AND abs(actual_ms - ?) <= ? ORDER BY abs(actual_ms - ?) LIMIT 1""",
                (start_ms, MATCH_WINDOW_MS, start_ms))
            how = "by time" if row else None
    if row is None:
        return {"args": args, "match": None, "how": None, "team": team,
                "note": "no TBA match for this log (untagged, or not in tba_match yet)"}
    d = json.loads(row["data"])
    red = [str(t) for t in d.get("red_teams") or []]
    blue = [str(t) for t in d.get("blue_teams") or []]
    ours = "red" if team in red else "blue" if team in blue else None
    match = {"match_key": row["uid"], "event_key": d.get("event_key"),
             "comp_level": d.get("comp_level"), "match_number": d.get("match_number"),
             "set_number": d.get("set_number"), "red_teams": red, "blue_teams": blue,
             "red_score": d.get("red_score"), "blue_score": d.get("blue_score"),
             "winning_alliance": d.get("winning_alliance") or None,
             "actual_time": d.get("actual_time"), "our_alliance": ours}
    if ours:
        match["partners"] = [t for t in (red if ours == "red" else blue) if t != team]
        match["opponents"] = blue if ours == "red" else red
        w = d.get("winning_alliance")
        match["result"] = None if d.get("red_score") is None else (
            "tie" if not w else "won" if w == ours else "lost")
    return {"args": args, "match": match, "how": how, "team": team}


def device_names() -> dict[str, Any]:
    rows = _rows("""SELECT device_type, can_id, label, subsystem FROM device
                    WHERE can_id >= 0 ORDER BY device_type, can_id""")
    rows, truncated = _cap(rows)
    return {"args": {}, "devices": rows, "truncated": truncated}


# ── the menu a model sees ────────────────────────────────────────────────

_UID = {"type": "string", "description": "A session uid from list_sessions."}
_SIGNAL = {"type": "string", "description": "Exact signal name, as list_signals gives it."}
_DTYPE = {"type": "string", "description": "e.g. TalonFX, CANcoder, Robot."}
_CAN = {"type": "integer", "description": "CAN id; -1 for the Robot pseudo-device."}


def _spec(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required}}


# Descriptions are the home server's docstrings, so a model sees the same menu.
SPECS: list[dict] = [
    _spec("list_sessions",
          "Robot log sessions, newest first. `since` is an ISO date (YYYY-MM-DD) on ingest time.",
          {"match_key": {"type": "string"}, "since": {"type": "string"},
           "limit": {"type": "integer"}}, []),
    _spec("session_overview",
          "The pit board's vitals for one session: battery, brownout, currents, temps, CAN, "
          "loop, faults.", {"session_uid": _UID}, ["session_uid"]),
    _spec("list_signals",
          "Which signals a session has, per device. `contains` filters signal names "
          "(case-insensitive).", {"session_uid": _UID, "contains": {"type": "string"}},
          ["session_uid"]),
    _spec("series_stats", "min / max / mean / last of one signal, per device.",
          {"session_uid": _UID, "signal": _SIGNAL, "device_type": _DTYPE, "can_id": _CAN},
          ["session_uid", "signal"]),
    _spec("series_1s",
          "One device's signal over time from the per-second rollup, as [t_s, value], "
          "evenly downsampled.",
          {"session_uid": _UID, "signal": _SIGNAL, "device_type": _DTYPE, "can_id": _CAN,
           "column": {"type": "string", "enum": ["v_min", "v_max", "v_avg"]},
           "max_points": {"type": "integer"}},
          ["session_uid", "signal", "device_type", "can_id"]),
    _spec("faults",
          "Fault events, sticky (latched) first, in the order a pit crew works through them. "
          "`summary` groups the latched ones by fault with a device count and a status "
          "(fault or warn, as the pit board shows them); `by_device` counts each "
          "device's latched faults.",
          {"session_uid": _UID}, ["session_uid"]),
    _spec("subsystems",
          "The team's mechanisms (from AdvantageKit signal names): peak stator/supply/PDH "
          "current, temp.", {"session_uid": _UID}, ["session_uid"]),
    _spec("compare_sessions", "The same signal's stats across several sessions, side by side.",
          {"session_uids": {"type": "array", "items": {"type": "string"}},
           "signal": _SIGNAL, "device_type": _DTYPE, "can_id": _CAN},
          ["session_uids", "signal"]),
    _spec("match_context",
          "Which TBA match a log was (the crew's match key, else the match nearest its "
          "start time), our alliance, partners, opponents, scores and result. Null when "
          "the match isn't known.", {"session_uid": _UID}, ["session_uid"]),
    _spec("device_names",
          "The team's CAN map: device_type, can_id, English label, subsystem (as named on "
          "the pits).", {}, []),
]

TOOLS = {
    "list_sessions": list_sessions, "session_overview": session_overview,
    "list_signals": list_signals, "series_stats": series_stats, "series_1s": series_1s,
    "faults": faults, "subsystems": subsystems, "compare_sessions": compare_sessions,
    "match_context": match_context, "device_names": device_names,
}


def call(name: str, arguments: dict | None) -> dict:
    """Run one tool by name. Bad names and bad arguments are answers, not exceptions."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"args": arguments or {}, "error": f"no tool {name!r}; tools: {', '.join(TOOLS)}"}
    allowed = next(t for t in SPECS if t["name"] == name)["input_schema"]["properties"]
    given = {k: v for k, v in (arguments or {}).items() if k in allowed}
    try:
        return fn(**given)
    except TypeError as e:
        return {"args": arguments or {}, "error": f"bad arguments: {e}"}
    except (ValueError, OverflowError) as e:
        return {"args": arguments or {}, "error": f"bad value: {e}"}
