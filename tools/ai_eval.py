"""
How well does the local model analyse real logs? Compare prompt and data
setups on the real model, side by side. Exit 0 (a report, not a gate).

    uv run tools/ai_eval.py                      # v2 vs v3 on Ollama's qwen3:8b
    uv run tools/ai_eval.py --variants v3,v3think --model qwen3:8b

A fresh scratch install imports TEST_LOGS/ (the fixture with real faults, an
AdvantageKit log with the new per-mechanism signals, a Rio hoot) and runs the
whole pipeline (analyst → checks → designer → checks) once per log per
variant, then prints what each run did: tool calls (which), how often the
checks sent it back, whether it published, and its findings in its own words.
Read the findings: the point is whether the crew would find them useful.

Variants:
  v2       the prompt and data before 2026-10-03: overview only, no guide, no lessons
  v3       overview + faults + subsystems + match up front, the fault guide, the crew's lessons
  v3think  v3 with Qwen3 thinking while it chooses tools
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

VARIANTS = {
    "v2": dict(grounding="overview", lessons=None, think_tools=False, guide=False),
    "v3": dict(grounding="full", lessons="crew", think_tools=False, guide=True),
    "v3think": dict(grounding="full", lessons="crew", think_tools=True, guide=True),
}
LESSONS = [   # the crew's real verdicts on this Mac's first runs (2026-10-03)
    "wrong: Investigate the firmware and configuration of multiple devices for "
    "StickyFaultField issues.",
    "not useful: Check the power supply and motor controllers for voltage issues causing "
    "BridgeBrownout on multiple devices.",
]


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--variants", default="v2,v3")
    ap.add_argument("--logs", default="fixture,akit,rio")
    opts = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="ai-eval-"))
    os.environ["PIT_DISPLAY_DATA"] = str(tmp)
    (tmp / "data").mkdir()
    import app.db.migrations  # noqa: F401
    from app.db import init_db
    from app.db.sync import bundle
    from app.robot import import_log
    db = init_db()
    logs = ROOT / "TEST_LOGS"
    uids = {}
    if "fixture" in opts.logs:
        sid = bundle.import_bundle(db.path, logs / "fixture_faults_2026-07-29.pitlog.zst", tmp)
        uids["fixture"] = db.fetchone("SELECT uid FROM log_session WHERE id = ?", (sid,))["uid"]
    for key, name in (("akit", "akit_26-08-17_02-43-54.wpilog"),
                      ("rio", "rio_2026-08-17_03-42-54.hoot")):
        if key in opts.logs:
            r = import_log(logs / name, db.path)
            uids[key] = db.fetchone("SELECT uid FROM log_session WHERE id = ?",
                                    (r.session_id,))["uid"]

    from app.ai import pipeline, trace
    from app.ai.local import LocalToolbox, SqliteSink
    from app.ai.ollama import Ollama
    llm = Ollama(num_ctx=16384)
    guide = pipeline.FAULT_GUIDE
    for variant in opts.variants.split(","):
        cfg = VARIANTS[variant]
        pipeline.FAULT_GUIDE = guide if cfg["guide"] else ""
        system = pipeline.ANALYST_SYSTEM
        if not cfg["guide"]:
            pipeline.ANALYST_SYSTEM = system.replace(guide, "")
        for key, uid in uids.items():
            t = time.time()
            res = pipeline.analyse(
                LocalToolbox(), SqliteSink(), llm, [uid], analyst=opts.model,
                grounding=cfg["grounding"], think_tools=cfg["think_tools"],
                lessons=LESSONS if cfg["lessons"] else None)
            steps = trace.steps(res.transcript)
            tools_used = [s.title for s in steps if s.kind == "tool"]
            retries = sum(s.kind == "retry" for s in steps)
            print(f"\n=== {variant} · {key} · {res.status} in {time.time() - t:.0f}s · "
                  f"{len(tools_used)} tool calls {tools_used} · sent back {retries}x"
                  + (f" · {res.reason[:160]}" if res.reason else ""), flush=True)
            for f in (res.insights or {}).get("findings") or []:
                m = f.get("metric") or {}
                print(f"   [{f.get('severity'):5}] {f.get('claim')}  "
                      f"({m.get('name')}: {m.get('value')} {m.get('unit') or ''})", flush=True)
        pipeline.ANALYST_SYSTEM = system
    pipeline.FAULT_GUIDE = guide
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
