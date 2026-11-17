#!/usr/bin/env python3
"""
Replay the crew's verdicts against what code would do now. Exit 0 (a report).

    uv run tools/ai_replay.py --data            # this Mac's database, read-only
    uv run tools/ai_replay.py --db path/to/pit_display.db

Every rated run on record is re-read and passed through the code-side rules
a run applies after the model writes (`playbook.drop_filler`, the playbook's
enforced lessons): no model, seconds to run. It prints, per prompt version,
how many rated findings each rule would leave out and how they were rated,
and the useful share of what's left: the number a rule or lesson change has
to move up without dropping anything the crew called useful. Then each
lesson's record (`playbook.evaluate`).

This is how a change to the learning loop is judged before it ships: the
filler rule went in because this report showed it drops 94 rated findings,
all 94 rated not useful (2026-11-03). Like `tools/anomaly_report.py --data`,
it reads this Mac's own data only when asked, and never writes to it.
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402


class _RO:
    """The read helpers `playbook.evaluate` wants, over a read-only file."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def fetchall(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def fetchone(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", action="store_true", help="this machine's own database")
    src.add_argument("--db", type=Path, help="a pit_display.db to read")
    ap.add_argument("--playbook", type=Path,
                    help="a playbook.json to apply (default: the one beside the database)")
    opts = ap.parse_args()
    path = opts.db
    if opts.data:
        from app import paths
        path = paths.data("data", "pit_display.db")
    if not path or not Path(path).is_file():
        print(f"No database at {path}")
        return 1
    from app.ai import playbook
    if opts.playbook:
        playbook.path = lambda: opts.playbook          # noqa: E731  (a report, not the app)
    elif not opts.data:      # a copied data tree: <root>/data/pit_display.db, <root>/playbook.json
        root = Path(path).parent.parent if Path(path).parent.name == "data" else Path(path).parent
        playbook.path = lambda: root / playbook.FILE_NAME   # noqa: E731
    lessons = playbook.active()
    enforced = [x for x in lessons if x.get("enforce") and x["kind"] == "avoid"]
    db = _RO(Path(path))

    ratings: dict[int, dict[str, str]] = collections.defaultdict(dict)
    for r in db.fetchall("SELECT run_id, finding_id, rating FROM analysis_feedback "
                         "WHERE finding_id <> '' AND rating IS NOT NULL"):
        ratings[r["run_id"]][r["finding_id"]] = r["rating"]

    # version → rule → rating → count; and what's left
    dropped = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    before = collections.defaultdict(collections.Counter)
    after = collections.defaultdict(collections.Counter)
    for r in db.fetchall("SELECT id, insights, stats FROM analysis_run "
                         "WHERE insights IS NOT NULL"):
        rated = ratings.get(r["id"])
        if not rated:
            continue
        try:
            ins = json.loads(r["insights"]) or {}
            version = int((json.loads(r["stats"] or "{}") or {}).get("prompt_version") or 1)
        except (ValueError, TypeError):
            continue
        rate = {f.get("id"): rated.get(f.get("id")) for f in ins.get("findings") or []
                if isinstance(f, dict)}
        for v in rate.values():
            if v:
                before[version][v] += 1
        for fid in playbook.drop_filler(ins):
            if rate.get(fid):
                dropped[version]["filler"][rate[fid]] += 1
        for d in playbook.enforce(ins, enforced):
            if rate.get(d["finding"]):
                dropped[version][d["lesson"]][rate[d["finding"]]] += 1
        for f in ins.get("findings") or []:
            if rate.get(f.get("id")):
                after[version][rate[f["id"]]] += 1

    def share(c: collections.Counter) -> str:
        n = sum(c.values())
        return f"{c['useful']}/{n} = {c['useful'] / n * 100:.0f}%" if n else "—"

    print(f"Rated runs in {path}\n")
    print(f"{'prompt':<8}{'useful now':<22}{'useful after the rules':<26}left out (rated)")
    for version in sorted(before):
        gone = "; ".join(f"{rule}: " + ", ".join(f"{k} {n}" for k, n in c.most_common())
                         for rule, c in dropped[version].items()) or "nothing"
        print(f"v{version:<7}{share(before[version]):<22}{share(after[version]):<26}{gone}")
    lost = sum(c["useful"] for v in dropped.values() for c in v.values())
    print(f"\nUseful findings the rules would leave out: {lost}"
          + ("  ← a rule is dropping what the crew wants" if lost else ""))

    print(f"\nThe playbook ({len(lessons)} in force, {len(enforced)} enforced by code)")
    records = playbook.evaluate(db, lessons)
    for x in lessons:
        flag = " [enforced]" if x in enforced else ""
        print(f"  {x['kind']:<6}{x['id']}{flag}\n        {records[x['id']].summary(x['kind'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
