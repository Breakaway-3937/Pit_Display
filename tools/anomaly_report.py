"""
Run the anomaly detector over a folder of real logs and report what it learns
and what it flags. Exit 0 (a report).

    uv run tools/anomaly_report.py .validation/home_bundles      # bundles (.pitlog.zst)
    uv run tools/anomaly_report.py /path/to/logs                 # or raw .hoot / .wpilog

Loads every log into a scratch database in recording order (never this Mac's
own data), then:

* **Normal, per bus:** each device's presence across logs, how often it was
  driven while the robot was enabled, and its usual peak temperature and
  stator current (median, range).
* **Flagged, per log:** what `app/robot/anomaly.py` finds in each, judged
  only against the logs recorded before it, as it would have been at the time.

Read the flags: each should be something the crew would want to know, or the
detector's thresholds need work.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--max-flags", type=int, default=8)
    opts = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="anomaly-report-"))
    os.environ["PIT_DISPLAY_DATA"] = str(tmp)
    (tmp / "data").mkdir()
    import app.db.migrations  # noqa: F401
    from app.db import init_db
    from app.db.sync import bundle
    from app.robot import anomaly, import_log
    db = init_db()

    files = sorted(p for p in opts.folder.rglob("*")
                   if p.is_file() and not p.name.startswith(".")
                   and p.name.endswith((".pitlog.zst", ".hoot", ".wpilog")))
    print(f"Loading {len(files)} log(s) from {opts.folder}…", flush=True)
    for i, f in enumerate(files, 1):
        try:
            if f.name.endswith(".pitlog.zst"):
                bundle.import_bundle(db.path, f, tmp)
            else:
                import_log(f, db.path)
        except Exception as e:
            print(f"  skipped {f.name}: {type(e).__name__}: {e}", flush=True)
        if i % 10 == 0:
            print(f"  {i}/{len(files)}", flush=True)

    from app.ai import tools
    from app.ai.local import ThreadDB
    with tools.using(ThreadDB()):
        sessions = db.fetchall(
            "SELECT id, uid, source_name, source_kind, device_serial, started_at, duration_s "
            "FROM log_session ORDER BY started_at, id")
        by_bus: dict[str, list] = defaultdict(list)
        for s in sessions:
            by_bus[anomaly.bus_of(s)].append(s)

        print("\n══ What normal looks like, per bus ══")
        for bus, rows in sorted(by_bus.items()):
            enabled = sum(1 for s in rows
                          if not (tools.match_phases(s["uid"]) or {}).get("never_enabled", True))
            print(f"\n{bus}: {len(rows)} logs, {enabled} with the robot enabled")
            ids = [s["id"] for s in rows]
            devs = db.fetchall(
                f"""SELECT d.id, d.device_type, d.can_id, d.label, COUNT(DISTINCT se.session_id) n
                    FROM device d JOIN series se ON se.device_id = d.id
                    WHERE se.session_id IN ({','.join('?' * len(ids))}) AND d.device_type <> 'Robot'
                    GROUP BY d.id ORDER BY d.device_type, d.can_id""", ids)
            for d in devs:
                name = d["label"] or f"{d['device_type']} {d['can_id']}"
                bits = [f"in {d['n']}/{len(rows)}"]
                for signal, label in (("DeviceTemp", "temp"), ("StatorCurrent", "stator A")):
                    vals = anomaly._extreme(db, ids, d["id"], signal, "max" if signal == "DeviceTemp" else "abs")
                    if vals:
                        bits.append(f"{label} peak median {statistics.median(vals):.1f} "
                                    f"(range {min(vals):.1f}–{max(vals):.1f})")
                print(f"   {name:<24} " + " · ".join(bits))

        print("\n══ Flagged, per log (judged against the logs before it) ══")
        flagged = 0
        for s in sessions:
            got = tools.call("anomalies", {"session_uid": s["uid"]})
            found = got.get("anomalies") or []
            if not found:
                continue
            flagged += 1
            ph = tools.match_phases(s["uid"]) or {}
            state = ("never enabled" if ph.get("never_enabled")
                     else f"enabled {ph.get('auto_s', 0) + ph.get('teleop_s', 0):.0f} s")
            print(f"\n{s['source_name']}  ({state}, vs {got.get('history_logs')} earlier logs)")
            for a in found[:opts.max_flags]:
                print(f"   [{a['severity']:5}] {a['kind']:<14} {a['device']}: {a['detail']}")
            if len(found) > opts.max_flags:
                print(f"   … and {len(found) - opts.max_flags} more")
        print(f"\n{flagged} of {len(sessions)} logs had something flagged.")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
