#!/usr/bin/env python3
"""
What the pit's CSA would say about every recording, without a model. Exit 0
(a report, for tuning `app/robot/events.py`, `incidents.py` and `app/ai/csa.py`).

    uv run tools/csa_report.py --data .validation/team_pull     # a pulled install
    uv run tools/csa_report.py --data /path/to/a/copied/data/tree

For each recording (every log recorded together: AdvantageKit, each CAN bus's
hoot, the Driver Station log), its issues worst first: how often, when, the
devices, and the knowledge base's candidate causes as code scores them on the
measured facts (the model's starting point). Read it like a CSA would: each
issue should be something worth a look, and the best-scored cause should be
the one you'd check first. If not, the thresholds or the knowledge need work.

Never point `--data` at this machine's own data/ (CLAUDE.md: checks and
reports read copies); it only reads.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, required=True, help="a data tree (with data/)")
    ap.add_argument("--issues", type=int, default=6)
    opts = ap.parse_args()
    os.environ["PIT_DISPLAY_DATA"] = str(opts.data.resolve())
    import app.db.migrations  # noqa: F401
    from app.db import init_db
    db = init_db()
    from app.ai import csa, tools
    from app.ai.local import ThreadDB

    seen: set[frozenset] = set()
    totals: dict[str, int] = {}
    with tools.using(ThreadDB()):
        for r in db.fetchall("SELECT uid, source_name FROM log_session WHERE uid IS NOT NULL "
                             "ORDER BY started_at, id"):
            got = tools.incidents(r["uid"])
            if "error" in got:
                continue
            key = frozenset(lg["uid"] for lg in got["logs"])
            if key in seen:
                continue
            seen.add(key)
            names = ", ".join(lg["name"] for lg in got["logs"])
            print(f"\n{names}\n  {got['n_events']} events, {got['n_incidents']} incidents, "
                  f"{len(got['issues'])} issues")
            for it in got["issues"][:opts.issues]:
                totals[it["primary"]] = totals.get(it["primary"], 0) + 1
                it = dict(it, recording_kinds=got.get("recording_kinds") or [])
                cands = csa.match(it, {"readings": it["readings"]})
                best = cands[0] if cands else None
                print(f"  [{it['severity']:5}] {it['primary']} ×{it['count']} at "
                      f"{', '.join(it['at'][:4])} ({'/'.join(it['phases'])})"
                      + (f" · {', '.join(it['devices'][:4])}" if it["devices"] else ""))
                b = it.get("before")
                if b:
                    print("          " + (f"new: not in the last {b['of']} recordings"
                                          if b.get("new") else
                                          f"also in {b['in']} of the last {b['of']} "
                                          f"recordings, {b['streak']} in a row"))
                if best:
                    print(f"          best by the facts: {best['id']} ({best['score']:+d}: "
                          f"{'; '.join(best['for'])[:110]})")
    print(f"\n{len(seen)} recordings. Issues by kind: "
          + ", ".join(f"{k} {n}" for k, n in sorted(totals.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
