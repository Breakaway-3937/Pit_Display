"""
Every test log imports completely, and identically every time. Exit 0/1.

    uv run tools/import_check.py
    uv run tools/import_check.py --hoot-runs 5

Each import goes into a fresh scratch data tree (a fresh install's database,
built by the migrations) and is compared against the log's known complete
counts: raw records read, rows stored, duration. A `.wpilog` is imported twice
(our reader must be deterministic); a `.hoot` goes through CTRE's owlet first
and is imported `--hoot-runs` times.

**Why the hoot runs repeat.** owlet 26.3.0 on macOS was measured
(2026-10-02) to cut the end off ~73% of `.wpilog` conversions of the same
hoot while reporting 100% and exiting 0: the short outputs are the complete
one minus its last seconds on every signal. The pit runs the Windows build;
this check is how to find out whether it does the same. A failure here is a
real one: an import that would silently lose the end of a match.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

# Known complete imports: (raw records, stored rows, duration s).
GOLDEN = {
    "akit_26-08-17_02-43-54.wpilog": (932381, 875223, 939.414),
    "akit_26-08-17_02-59-21.wpilog": (389619, 364485, 391.411),
    # The complete owlet conversion (9,501,728 bytes on macOS, owlet 26.3.0).
    "rio_2026-08-17_03-42-54.hoot": (590619, 36781, 42.669),
}

_IMPORT = r"""
import os, sys, json
from pathlib import Path
os.makedirs(os.environ["PIT_DISPLAY_DATA"] + "/data", exist_ok=True)
import app.db.migrations
from app.db import init_db
db = init_db()
from app.robot.ingest import import_log
r = import_log(Path(sys.argv[1]), db.path)
d = db.fetchone("SELECT duration_s FROM log_session")[0]
print(json.dumps([r.raw_rows, r.stored_rows, round(d, 3)]))
"""

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    if not ok:
        _failures.append(name)


def import_once(log: Path, scratch: Path) -> tuple | None:
    """One import in its own process and data tree (as the app does it)."""
    shutil.rmtree(scratch, ignore_errors=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PIT_SECRET_")}
    env["PIT_DISPLAY_DATA"] = str(scratch)
    env["PIT_LOG_SCRATCH"] = str(scratch / "owlet")
    out = subprocess.run([sys.executable, "-c", _IMPORT, str(log)], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    import json
    last = (out.stdout.strip().splitlines() or [""])[-1]
    try:
        return tuple(json.loads(last))
    except ValueError:
        print((out.stdout + out.stderr)[-800:])
        return None


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hoot-runs", type=int, default=3)
    opts = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="import-check-"))
    try:
        for name, golden in GOLDEN.items():
            log = ROOT / "TEST_LOGS" / name
            if not log.is_file():
                check(f"{name} present", False, str(log))
                continue
            runs = opts.hoot_runs if log.suffix == ".hoot" else 2
            print(f"\n{name}  ({runs} imports)")
            results = [import_once(log, tmp / f"run{i}") for i in range(runs)]
            complete = sum(1 for r in results if r == golden)
            short = [r for r in results if r != golden]
            check(f"imports completely every time: {complete} of {runs} matched "
                  f"{golden[0]:,} raw / {golden[1]:,} stored / {golden[2]} s",
                  complete == runs,
                  "" if not short else "short: " + ", ".join(
                      f"{r[0]:,} raw, {r[2]} s" if r else "failed" for r in short)
                  + (" (owlet cut the end off: see this file's docstring)"
                     if log.suffix == ".hoot" else ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'All imports complete.' if not _failures else f'{len(_failures)} failed.'}")
    return 0 if not _failures else 1


if __name__ == "__main__":
    sys.exit(main())
