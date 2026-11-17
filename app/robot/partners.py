"""
Motors that turn together and should share the work: one that stops pulling
its weight is a fault the robot itself never reports.

Brayden, 2026-11-03: "We ran a good chunk of the year last year with only one
of our two shooter motors running; that should've been flagged by temps not
matching." When two motors share a shaft, a belt or a gear train, a dead one
is **back-driven**: it still turns at its partner's speed, so it isn't
`missing` and isn't `not_driven` (anomaly.py), but it draws no current and
never warms up. So:

1. **Find the twins from the log, not from labels.** Two motors whose speeds
   move together over the seconds either one was turning (Pearson r of
   |RotorVelocity| ≥ `TWIN_R`, at least `MIN_MOVING_S` such seconds) turn
   together; the ratio of their speeds is the gearing. The crew's subsystem
   labels were empty on every device (2026-11-03), and this needs none. On
   the team's logs it finds TalonFX 15~16 (equal speed and current, every Rio
   log: the shooter), 7~13~14 (geared, 0.7 / 1.5) and the swerve drives.
2. **Compare the work.** Mean |StatorCurrent| over those seconds, as the
   weaker's share of the stronger's (`share`), and each one's temperature
   rise.
3. **Judge.** `partner_idle` (fault): the stronger pulls ≥ `IDLE_STRONG_A`
   and the weaker ≤ `IDLE_SHARE` of it: one motor of a pair is doing almost
   nothing. `partner_mismatch` (warn): the pair has twinned in ≥
   `MIN_HISTORY` earlier logs of this bus and the share left its own usual
   range (below `DROP` × its median, or the pair's roles swapped): something
   changed between them (a belt, a failing motor, a follower config).

Healthy pairs on the team's 63 logs: 15~16 share 0.85-1.0, 13~14 0.47-0.54
(steady, geared), drives 0.75-1.0; none is flagged. Thresholds are named
constants; retune with `tools/anomaly_report.py`, never per log.

Reads only `samples.sample_1s` (plain SQL: home runs this module over SQL
Server through `anomaly.find`). No Qt.
"""

from __future__ import annotations

import itertools
import statistics

TWIN_R = 0.97            # |velocity| correlation that means "turn together"
MOVING_RPS = 2.0         # a second counts when either motor turns this fast
MIN_MOVING_S = 20        # seconds both were watched turning
IDLE_STRONG_A = 8.0      # the stronger motor's mean current for partner_idle…
IDLE_SHARE = 0.2         # …and the weaker's share of it at or below this
MISMATCH_STRONG_A = 5.0  # partner_mismatch needs the pair working at least this hard
DROP = 0.5               # a share below half the pair's usual is a change
MIN_HISTORY = 3          # earlier logs the pair twinned in, for partner_mismatch
MOTORS = ("TalonFX", "TalonFXS")

_CACHE: dict[str, dict] = {}       # session uid → pairs (a log never changes)


def _series(db, session_id: int, device_id: int, name: str) -> int | None:
    r = db.fetchone("""SELECT se.id FROM series se JOIN signal g ON g.id = se.signal_id
                       WHERE se.session_id = ? AND se.device_id = ? AND g.name = ?""",
                    (session_id, device_id, name))
    return r["id"] if r else None


def _per_second(db, series_id: int | None) -> dict[int, float]:
    if series_id is None:
        return {}
    return {r["t_s"]: r["v_avg"] for r in db.fetchall(
        "SELECT t_s, v_avg FROM samples.sample_1s WHERE series_id = ?", (series_id,))
        if r["v_avg"] is not None}


def _rise(db, series_id: int | None) -> float | None:
    if series_id is None:
        return None
    r = db.fetchone("SELECT v_min, v_max FROM series WHERE id = ?", (series_id,))
    if r is None or r["v_min"] is None or r["v_max"] is None:
        return None
    return round(r["v_max"] - r["v_min"], 4)


def pairs(db, session_id: int) -> dict[tuple, dict]:
    """
    Every twin pair in one log: (lower key, higher key) → {r, seconds,
    speed_ratio, amps (a, b), share (b's current / a's), rise (a, b)}. A key
    is (device_type, can_id); `a` is the first of the sorted pair.
    """
    row = db.fetchone("SELECT uid FROM log_session WHERE id = ?", (session_id,))
    key = row["uid"] if row and row["uid"] else None
    if key is not None and key in _CACHE:
        return _CACHE[key]
    devs = {}
    for d in db.fetchall(
            """SELECT DISTINCT d.id, d.device_type, d.can_id FROM device d
               JOIN series se ON se.device_id = d.id WHERE se.session_id = ?""", (session_id,)):
        if d["device_type"] in MOTORS:
            devs[(d["device_type"], d["can_id"])] = d["id"]
    vel = {k: _per_second(db, _series(db, session_id, i, "RotorVelocity")) for k, i in devs.items()}
    cur = {k: _per_second(db, _series(db, session_id, i, "StatorCurrent")) for k, i in devs.items()}
    out: dict[tuple, dict] = {}
    for a, b in itertools.combinations(sorted(devs), 2):
        ts = sorted(t for t in vel[a].keys() & vel[b].keys()
                    if abs(vel[a][t]) >= MOVING_RPS or abs(vel[b][t]) >= MOVING_RPS)
        if len(ts) < MIN_MOVING_S:
            continue
        va, vb = [abs(vel[a][t]) for t in ts], [abs(vel[b][t]) for t in ts]
        try:
            r = statistics.correlation(va, vb)
        except statistics.StatisticsError:
            continue                          # one of them never changed speed
        if r < TWIN_R:
            continue
        ia = statistics.mean(abs(cur[a].get(t, 0.0)) for t in ts)
        ib = statistics.mean(abs(cur[b].get(t, 0.0)) for t in ts)
        out[(a, b)] = {
            "r": round(r, 4), "seconds": len(ts),
            "speed_ratio": round(statistics.mean(vb) / max(statistics.mean(va), 1e-9), 4),
            "amps": (round(ia, 4), round(ib, 4)),
            "share": round(ib / ia, 4) if ia > 1e-6 else None,
            "rise": (_rise(db, _series(db, session_id, devs[a], "DeviceTemp")),
                     _rise(db, _series(db, session_id, devs[b], "DeviceTemp"))),
        }
    if key is not None:
        _CACHE[key] = out
    return out


def find(db, session_id: int, hist_ids: list[int], labels: dict[tuple, str]) -> list[dict]:
    """Detector items (anomaly.py's shape) for this log; `labels` names devices."""
    now = pairs(db, session_id)
    if not now:
        return []
    history = {sid: pairs(db, sid) for sid in hist_ids}
    out, named = [], set()
    # Strongest twins first, so a motor in a cluster (the swerve drives) is
    # judged against its closest partner and reported once.
    for (a, b), p in sorted(now.items(), key=lambda kv: -kv[1]["r"]):
        ia, ib = p["amps"]
        strong, weak = (a, b) if ia >= ib else (b, a)
        i_strong, i_weak = max(ia, ib), min(ia, ib)
        if weak in named:
            continue
        share = i_weak / i_strong if i_strong > 1e-6 else None
        if share is None:
            continue
        rise = dict(zip((a, b), p["rise"]))
        past = [h[(a, b)]["share"] for h in history.values()
                if (a, b) in h and h[(a, b)]["share"] is not None]
        base = {"device": labels.get(weak, f"{weak[0]} {weak[1]}"),
                "device_type": weak[0], "can_id": weak[1], "subsystem": None,
                "partner": labels.get(strong, f"{strong[0]} {strong[1]}"),
                "devices": [labels.get(weak, f"{weak[0]} {weak[1]}"),
                            labels.get(strong, f"{strong[0]} {strong[1]}")],
                "signal": "StatorCurrent", "value": round(i_weak, 4), "unit": "A",
                "partner_value": round(i_strong, 4), "share": round(share, 4),
                "speed_ratio": p["speed_ratio"], "seconds": p["seconds"],
                "heat_rise": rise.get(weak), "partner_heat_rise": rise.get(strong),
                "must_cover": True}
        heat = ""
        if rise.get(weak) is not None and rise.get(strong) is not None:
            heat = (f"; it warmed {rise[weak]:g} °C against its partner's "
                    f"{rise[strong]:g} °C")
        if i_strong >= IDLE_STRONG_A and share <= IDLE_SHARE:
            out.append(base | {
                "kind": "partner_idle", "severity": "fault",
                "detail": f"turns with {base['partner']} (speeds match, r {p['r']:g}, over "
                          f"{p['seconds']} s) but drew {i_weak:.1f} A to its "
                          f"{i_strong:.1f} A{heat}: it's being turned, not driving. Check its "
                          "power, its motor leads, and that the code drives it (a follower "
                          "never set, or set to the wrong leader)"})
            named.add(weak)
            continue
        if len(past) < MIN_HISTORY or i_strong < MISMATCH_STRONG_A:
            continue
        # The share in the pair's fixed order (b's current / a's), as history keeps it.
        mine = p["share"]
        usual = statistics.median(past)
        if mine is None or usual <= 0:
            continue
        low, high = min(past), max(past)
        if mine < DROP * usual or mine > usual / DROP:
            out.append(base | {
                "kind": "partner_mismatch", "severity": "warn",
                "normal": {"median": round(usual, 4), "low": round(low, 4),
                           "high": round(high, 4), "logs": len(past)},
                "detail": f"turns with {base['partner']} and normally shares the work in a "
                          f"steady ratio ({_r(usual)}, {len(past)} earlier logs); this log "
                          f"{_r(mine)} ({i_weak:.1f} A to {i_strong:.1f} A){heat}. Something "
                          "changed between them: a belt or gear slipping, a motor starting to "
                          "fail, or a follower's configuration"})
            named.add(weak)
    return out


def _r(x: float) -> str:
    return f"{x:.2f}"
