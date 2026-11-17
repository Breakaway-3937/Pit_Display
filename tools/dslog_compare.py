#!/usr/bin/env python3
"""
Every Driver Station log, checked: against itself, and against the robot's own
logs wherever they overlap. Exit 0 (a report, for trusting `app/robot/dslog.py`).

    uv run tools/dslog_compare.py --data <a copy of a data tree with DS logs in it>

Per DS log, by itself:
  * its length (records at 50 Hz) against the gap to the next DS log's start:
    a log longer than the gap means the clocks or the decoding are off;
  * every second the DS's brownout flag is up, the battery it read then (a real
    brownout sits under ~7.5 V; a flag at 12 V would be a misread bit);
  * the match phases (`match_phases`: auto and teleop seconds, the 3 s gap),
    and the match the FMS message names.
Against each robot log recorded at the same time (UTC, as the robot names them):
  * the clock offset that lines the battery up (the DS's clock vs the file name's);
  * AdvantageKit: battery vs `SystemStats/BatteryVoltage`, total current vs
    `RealOutputs/Robot Total Current Amps`, enabled vs `DriverStation/Enabled`;
  * hoots: battery vs the controllers' median `SupplyVoltage` (it reads a
    little lower: the wiring between), the PDH total vs the controllers'
    summed `SupplyCurrent` (lower: not everything on the PDH is a TalonFX).

Never point `--data` at this machine's own data/ (CLAUDE.md); it only reads.
"""

from __future__ import annotations

import argparse
import os
import statistics as st
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

SHIFT_S = 60          # the clock offsets tried, ± seconds
LOADED_V = 11.5       # the robot's battery under this: under load, where the clock is fitted
MIN_LOADED_S = 20
MIN_CUR_S = 20        # seconds over 30 A needed to compare current
BROWNOUT_SANE_V = 8.5 # the DS's 20 ms reading at a real brownout stays under this
MIN_FIT_S = 60        # overlap needed to fit the clock (a short log's flat battery fits anything)


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, required=True, help="a data tree (with data/)")
    opts = ap.parse_args()
    os.environ["PIT_DISPLAY_DATA"] = str(opts.data.resolve())
    import app.db.migrations  # noqa: F401
    from app.db import init_db
    db = init_db()
    from app.ai import tools
    from app.ai.local import ThreadDB

    def per_second(sid: int, signal: str, col: str, device_type: str | None = None
                   ) -> dict[int, float]:
        """{t_s: value} of one signal of one session, merged over devices with
        `device_type` (the median), else the Robot pseudo-device."""
        rows = db.fetchall(
            f"""SELECT r.t_s, r.{col} AS v, se.device_id FROM samples.sample_1s r
                JOIN series se ON se.id = r.series_id JOIN signal g ON g.id = se.signal_id
                JOIN device d ON d.id = se.device_id
                WHERE se.session_id = ? AND g.name = ? AND d.device_type = ?""",
            (sid, signal, device_type or "Robot"))
        by: dict[int, list[float]] = {}
        for r in rows:
            if r["v"] is not None:
                by.setdefault(r["t_s"], []).append(r["v"])
        return {t: st.median(v) for t, v in by.items()}

    def steps(sid: int, signal: str) -> list[tuple[float, float]]:
        """A Robot signal's change points: storage keeps only changes, so a
        boolean has 1 s rows only where it flipped (per_second can't hold it)."""
        return [(x["t_ms"] / 1000.0, x["v"]) for x in db.fetchall(
            """SELECT s.t_ms, s.v FROM samples.sample s JOIN series se ON se.id = s.series_id
               JOIN signal g ON g.id = se.signal_id JOIN device d ON d.id = se.device_id
               WHERE se.session_id = ? AND g.name = ? AND d.device_type = 'Robot'
               ORDER BY s.t_ms, s.ord""", (sid, signal)) if x["v"] is not None]

    def at(pts: list[tuple[float, float]], t: float) -> float | None:
        v = None
        for pt, pv in pts:
            if pt > t:
                break
            v = pv
        return v

    def summed(sid: int, signal: str, device_type: str) -> dict[int, float]:
        rows = db.fetchall(
            """SELECT r.t_s, SUM(r.v_avg) AS v FROM samples.sample_1s r
               JOIN series se ON se.id = r.series_id JOIN signal g ON g.id = se.signal_id
               JOIN device d ON d.id = se.device_id
               WHERE se.session_id = ? AND g.name = ? AND d.device_type = ? GROUP BY r.t_s""",
            (sid, signal, device_type))
        return {r["t_s"]: r["v"] for r in rows if r["v"] is not None}

    ds = db.fetchall("""SELECT id, uid, source_name, started_at, duration_s, match_key
                        FROM log_session WHERE source_kind = 'dslog' ORDER BY started_at""")
    robot = db.fetchall("""SELECT id, uid, source_name, source_kind, started_at, duration_s
                           FROM log_session WHERE source_kind IN ('wpilog', 'hoot')
                             AND started_at IS NOT NULL""")
    flags = {"length": 0, "brownout_flag": 0, "phases": 0}
    compared = []
    print(f"{len(ds)} Driver Station logs\n")
    with tools.using(ThreadDB()):
        for n, r in enumerate(ds):
            a = _epoch(r["started_at"])
            b = a + float(r["duration_s"] or 0)
            notes = []
            # ── by itself ────────────────────────────────────────────────
            if n + 1 < len(ds):
                gap = _epoch(ds[n + 1]["started_at"]) - a
                if (r["duration_s"] or 0) > gap + 5:
                    notes.append(f"LONGER than the gap to the next log ({r['duration_s']:.0f} s "
                                 f"vs {gap:.0f} s)")
                    flags["length"] += 1
            bat = per_second(r["id"], "DS/BatteryVoltage", "v_min")
            bo = per_second(r["id"], "DS/Brownout", "v_max")
            en_pts = steps(r["id"], "DS/Enabled")
            co_pts = steps(r["id"], "DS/Comms")
            bo_v, off_flags = [], 0
            for t, v in bo.items():
                if v < 0.5 or t not in bat:
                    continue
                # Disabled, and the DS loses the robot within 5 s: the battery
                # being pulled (its voltage fades), not a brownout.
                if not (at(en_pts, t) or 0) >= 0.5 and any(
                        (at(co_pts, t + k) or 0) < 0.5 for k in range(1, 6)):
                    off_flags += 1
                    continue
                bo_v.append(bat[t])
            if bo_v and max(bo_v) > BROWNOUT_SANE_V:
                notes.append(f"BROWNOUT FLAG at {max(bo_v):.2f} V while running")
                flags["brownout_flag"] += 1
            flags["power_off"] = flags.get("power_off", 0) + off_flags
            ph = tools.match_phases(r["uid"])
            auto, tele = ph.get("auto_s") or 0, ph.get("teleop_s") or 0
            full = r["match_key"] and auto > 0
            if full and not (19 <= auto <= 21 and 138 <= tele <= 141):
                notes.append(f"match time: auto {auto:g} s, teleop {tele:g} s (of 20 + 140)")
                flags["phases"] += 1
            line = (f"{r['source_name'][:19]}  {r['started_at'][5:16]} UTC  "
                    f"{(r['duration_s'] or 0):6.0f} s  {r['match_key'] or '-':5} "
                    f"auto {auto:5.1f} teleop {tele:5.1f}  brownout s {len(bo_v):3d}"
                    + (f" (battery {min(bo_v):.2f}-{max(bo_v):.2f} V)" if bo_v else "")
                    + (f"  power-off flags {off_flags}" if off_flags else ""))
            print(line)
            for x in notes:
                print("      ! " + x)
            # ── against the robot's logs ─────────────────────────────────
            ds_cur = per_second(r["id"], "DS/TotalCurrent", "v_max")
            ds_avg = per_second(r["id"], "DS/TotalCurrent", "v_avg")
            # The robot's logs that overlap: each AdvantageKit log alone, and the
            # hoots of one recording (one per CAN bus) together, so the motors
            # summed are all of them.
            near = [lg for lg in robot
                    if (lg["duration_s"] or 0) >= MIN_FIT_S
                    and _epoch(lg["started_at"]) <= b
                    and _epoch(lg["started_at"]) + float(lg["duration_s"]) >= a]
            groups: list[list] = [[lg] for lg in near if lg["source_kind"] == "wpilog"]
            for lg in sorted((x for x in near if x["source_kind"] == "hoot"),
                             key=lambda x: x["started_at"]):
                g = next((g for g in groups if g[0]["source_kind"] == "hoot" and abs(
                    _epoch(g[0]["started_at"]) - _epoch(lg["started_at"])) <= 60), None)
                (g.append(lg) if g else groups.append([lg]))
            for group in groups:
                lg = group[0]
                c = _epoch(lg["started_at"])
                off = c - a                       # robot t=0 in DS seconds
                if lg["source_kind"] == "wpilog":
                    rb = per_second(lg["id"], "SystemStats/BatteryVoltage", "v_min")
                    rc = per_second(lg["id"], "RealOutputs/Robot Total Current Amps", "v_max")
                    re_pts = steps(lg["id"], "DriverStation/Enabled")
                    what, cur_ds = "AdvantageKit", ds_cur
                else:
                    rb, rc = {}, {}
                    for h in group:              # every bus, on the first one's clock
                        o = round(_epoch(h["started_at"]) - c)
                        for t, v in per_second(h["id"], "SupplyVoltage", "v_min",
                                               "TalonFX").items():
                            rb[t + o] = min(rb.get(t + o, v), v)
                        for t, v in summed(h["id"], "SupplyCurrent", "TalonFX").items():
                            rc[t + o] = rc.get(t + o, 0.0) + v
                    re_pts = []
                    what, cur_ds = f"hoots, {len(group)} buses", ds_avg
                if len(rb) < MIN_FIT_S:
                    continue
                # The clock is fitted on the seconds the battery was under load
                # (it moves then); hours of a flat 12.5 V fit any offset.
                loaded = {t: v for t, v in rb.items() if v < LOADED_V}
                fit_on = loaded if len(loaded) >= MIN_LOADED_S else rb
                best = None
                for shift in range(-SHIFT_S, SHIFT_S + 1):
                    d = [abs(bat[int(t + off + shift)] - v) for t, v in fit_on.items()
                         if int(t + off + shift) in bat]
                    if len(d) >= min(10, len(fit_on)):
                        score = st.mean(d)
                        if best is None or score < best[0]:
                            best = (score, shift)
                if best is None:
                    continue
                shift = best[1]
                diffs = [abs(bat[int(t + off + shift)] - v) for t, v in rb.items()
                         if int(t + off + shift) in bat]
                m = st.median(diffs)
                fitted = "load" if fit_on is loaded else "flat battery: offset unsure"
                k = lambda t: int(t + off + shift)  # noqa: E731
                cur = [(v, cur_ds[k(t)]) for t, v in rc.items() if k(t) in cur_ds and v > 30]
                if len(cur) < MIN_CUR_S:
                    cur = []                      # a few seconds of current say nothing
                en = []
                if re_pts and en_pts:
                    span = max(t for t, _ in rb.items())
                    for t in range(5, int(span)):   # every second, from both step functions
                        x, y = at(re_pts, t), at(en_pts, t + off + shift)
                        if x is not None and y is not None:
                            en.append((x >= 0.5) == (y >= 0.5))
                res = {"ds": r["source_name"], "robot": lg["source_name"], "kind": what,
                       "group": len(group),
                       "shift": shift, "fit": fitted, "n": len(diffs), "bat_med": m,
                       "bat_p95": sorted(diffs)[int(len(diffs) * 0.95) - 1],
                       "cur_n": len(cur),
                       "cur_ratio": st.median(a2 / b2 for a2, b2 in cur if b2) if cur else None,
                       "cur_med": st.median(abs(a2 - b2) for a2, b2 in cur) if cur else None,
                       "en": (sum(en) / len(en)) if en else None}
                compared.append(res)
                print(f"      vs {lg['source_name'][:40]:40} [{what}] clock {shift:+d} s "
                      f"({fitted}), "
                      f"{len(diffs)} s: battery |diff| median {m * 1000:.0f} mV, p95 "
                      f"{res['bat_p95'] * 1000:.0f} mV"
                      + (f"; current over 30 A ({len(cur)} s): robot/DS median "
                         f"{res['cur_ratio']:.3f}, |diff| {res['cur_med']:.1f} A" if cur else "")
                      + (f"; enabled agrees {res['en'] * 100:.1f}%" if en else ""))
    sure = [c for c in compared if c["fit"] == "load"]
    print(f"\nClock fitted on load: {len(sure)} of {len(compared)} comparisons (the rest had a "
          f"flat battery the whole overlap: a pit recording; left out below).")
    ak = [c for c in sure if c["kind"] == "AdvantageKit"]
    ho = [c for c in sure if c["kind"] != "AdvantageKit"]
    print(f"\n{len(ds)} DS logs. Longer than their gap: {flags['length']}. Brownout flag "
          f"above 8.5 V while running: {flags['brownout_flag']} (power-off flags set aside: "
          f"{flags.get('power_off', 0)}). Full matches off 20 + 140 s: {flags['phases']}.")
    if ak:
        print(f"Against AdvantageKit ({len(ak)} logs, {sum(c['n'] for c in ak)} s): battery "
              f"|diff| median of medians {st.median(c['bat_med'] for c in ak) * 1000:.0f} mV, "
              f"worst {max(c['bat_med'] for c in ak) * 1000:.0f} mV; clock offsets "
              f"{sorted({c['shift'] for c in ak})} s"
              + (f"; current ratio {min(c['cur_ratio'] for c in ak if c['cur_ratio']):.3f}-"
                 f"{max(c['cur_ratio'] for c in ak if c['cur_ratio']):.3f}"
                 if any(c["cur_ratio"] for c in ak) else "")
              + (f"; enabled agrees {min(c['en'] for c in ak if c['en'] is not None) * 100:.1f}"
                 f"-{max(c['en'] for c in ak if c['en'] is not None) * 100:.1f}%"
                 if any(c["en"] is not None for c in ak) else ""))
    if ho:
        print(f"Against hoots ({len(ho)} recordings, every bus): battery vs controller supply "
              f"median of medians {st.median(c['bat_med'] for c in ho) * 1000:.0f} mV; motors' "
              f"share of the PDH total "
              + (f"{min(c['cur_ratio'] for c in ho if c['cur_ratio']):.2f}-"
                 f"{max(c['cur_ratio'] for c in ho if c['cur_ratio']):.2f}"
                 if any(c["cur_ratio"] for c in ho) else "n/a"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
