"""
The playbook: what the analyst has learned from this crew's verdicts, kept,
measured and enforced (Brayden, 2026-11-03: "a hermes-esque concept", after
Nous Research's Hermes agent, whose loop is *do the task → reflect → write
the lesson down → load it next time → keep what works*).

Before this, the crew's verdicts reached the model as `feedback.lessons()`:
the last eight rated claims, verbatim ("not useful: TalonFX 15 …"), with no
reason and no memory past eight. An 8B model can't generalise from that, and
nothing checked whether the lines changed anything. The playbook closes the
loop in four steps, each owned by code except the one that needs words:

1. **Learn** (`curate.py`, the model, when the engine is idle): new verdicts,
   with the crew's reasons, become short general lessons ("a reading that's
   within limits is not a finding"). Code refuses a lesson that isn't
   grounded in the verdicts it cites (`curate.check_changes`).
2. **Load** (`select`, every run): the lessons whose topic appears in what
   this log's data says, a handful, never the whole book (a 16k context).
3. **Measure** (`evaluate`): for each lesson, the crew's verdicts on findings
   *about its topic*, in runs that were given it versus runs that weren't.
   Derived from synced rows, so every machine computes the same record.
4. **Enforce** (`enforce`): an `avoid` lesson the model keeps ignoring
   (still rated not useful after it was given) is applied by code: matching
   findings are dropped, the way `pipeline._drop_unwanted` drops current
   limits. Only drops, **never a fault, never a detector finding, never the
   last finding**, and every drop is recorded in the run's stats.

A lesson carries **no figure of its own** unless a crew verdict or note it
was learned from wrote that number: "every number is real" holds here too.

Where it lives: a team setting document (`playbook`, `playbook.json` beside
the database, synced like the robot brief): `lessons` (learned ones),
`retired_seeds` (built-in lessons the team switched off), `curated_at` (the
watermark: verdicts rated after it haven't been learned from) and `history`
(each change, by whom). `SEEDS` are built in, distilled by hand from the 420
verdicts on record (2026-11-03); they're code, like `FAULT_GUIDE`, so a fresh
machine never pushes them over the team's book.

No Qt: home imports this from a checkout, as it does the pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app import paths

FILE_NAME = "playbook.json"
KINDS = ("avoid", "keep")
MAX_RULE = 220
MAX_TOPIC = 5
MAX_LEARNED = 20          # active learned lessons; past it the curator must merge
MAX_HISTORY = 40
SELECT_LIMIT = 8          # lessons per run
SELECT_CHARS = 1600       # ~450 tokens of a 16k context
# Enforcing an `avoid` lesson by code needs this much evidence that the model
# doesn't follow it: matching findings rated after it was given, and the share
# of them the crew called not useful or wrong.
ENFORCE_MIN_RATED = 3
ENFORCE_MIN_SHARE = 0.75
ENFORCE_MIN_SOURCES = 3   # verdicts it was learned from (or a seed)

DEFAULTS: dict[str, Any] = {"lessons": [], "retired_seeds": [], "curated_at": "",
                            "history": []}

# Distilled from the crew's and the validation pass's verdicts on 303 v2 and
# 117 v5/v6 findings (2026-11-03). Each says what the verdicts said, and
# `topic` is the words a finding about it contains (normalised, `_norm`).
SEEDS: list[dict] = [
    {"id": "seed_normal_is_not_a_finding", "kind": "avoid",
     "rule": "A reading that's normal (within limits, no event, nothing detected) is not a "
             "finding. When nothing needs attention, write one all-clear finding instead.",
     "topic": ["withinnormal"], "seed": True,
     "why": "every ok finding written beside a warn or fault was rated not useful (94 of 94)"},
    {"id": "seed_threshold_is_not_a_reading", "kind": "avoid",
     "rule": "A threshold (the roboRIO's brownout voltage, a configured limit) is not a "
             "measurement: never report it as what the robot did.",
     "topic": ["threshold"], "seed": True,
     "why": "a finding reporting the 6.8 V brownout threshold as the battery was rated wrong"},
    {"id": "seed_reboot_while_enabled", "kind": "keep",
     "rule": "BootDuringEnable is always worth a finding: name the controller and say to "
             "check its power and CAN connectors and its breaker.",
     "topic": ["bootduringenable"], "seed": True,
     "why": "rated useful every time it was reported (28 of 28)"},
    {"id": "seed_sensor_out_of_sync", "kind": "keep",
     "rule": "RemoteSensorDataInvalid or FusedSensorOutOfSync means the CANcoder a "
             "controller relies on isn't reporting or agreeing: check its wiring, ID and "
             "configuration.",
     "topic": ["remotesensor", "fusedsensor", "sensordata"], "seed": True,
     "why": "rated useful every time (23 of 23)"},
    {"id": "seed_unlicensed_feature", "kind": "keep",
     "rule": "UnlicensedFeatureInUse on a controller using a fused CANcoder is real: the "
             "feature needs Phoenix Pro, and without the licence the fusion is off.",
     "topic": ["unlicensed"], "seed": True,
     "why": "rated useful 29 of 31 times"},
]


# ── the document ─────────────────────────────────────────────────────────

def path():
    return paths.data(FILE_NAME)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def clean_lesson(x: Any) -> dict | None:
    """A stored lesson in its canonical shape, or None if it can't be one."""
    if not isinstance(x, dict):
        return None
    lid = re.sub(r"[^a-z0-9_]+", "_", str(x.get("id") or "").lower()).strip("_")[:48]
    rule = " ".join(str(x.get("rule") or "").split())[:MAX_RULE]
    kind = x.get("kind") if x.get("kind") in KINDS else None
    topic = [t for t in (_norm(t) for t in x.get("topic") or []) if t][:MAX_TOPIC]
    if not lid or not rule or kind is None or not topic:
        return None
    return {
        "id": lid, "rule": rule, "kind": kind, "topic": topic,
        "learned_from": [str(v) for v in x.get("learned_from") or []][:30],
        "by": str(x.get("by") or "")[:60], "added": str(x.get("added") or "")[:20],
        "enforce": bool(x.get("enforce")), "retired": bool(x.get("retired")),
        "why": str(x.get("why") or "")[:200],
    }


def load() -> dict[str, Any]:
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    stored = stored if isinstance(stored, dict) else {}
    lessons = [c for c in (clean_lesson(x) for x in stored.get("lessons") or []) if c]
    return {
        "lessons": lessons,
        "retired_seeds": [str(s) for s in stored.get("retired_seeds") or []
                          if any(s == x["id"] for x in SEEDS)],
        "curated_at": str(stored.get("curated_at") or ""),
        "history": [h for h in stored.get("history") or [] if isinstance(h, dict)][-MAX_HISTORY:],
    }


def save(**changes: Any) -> dict[str, Any]:
    values = load()
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    values["lessons"] = [c for c in (clean_lesson(x) for x in values["lessons"]) if c]
    values["history"] = list(values["history"])[-MAX_HISTORY:]
    try:
        path().write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    except OSError:
        pass          # a read-only data dir is already reported by --self-check
    return load()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def note(doc: dict, by: str, what: str, **detail) -> None:
    """Append a history entry (in place; the caller saves)."""
    doc.setdefault("history", []).append({"at": _now(), "by": by, "what": what[:300], **detail})


def active(doc: dict | None = None) -> list[dict]:
    """Every lesson in force: the seeds the team kept, then the learned ones."""
    doc = doc or load()
    enforced = {x["id"] for x in doc["lessons"] if x.get("enforce")}
    seeds = [dict(s, enforce=s["id"] in enforced or bool(s.get("enforce")))
             for s in SEEDS if s["id"] not in doc["retired_seeds"]]
    learned = [x for x in doc["lessons"] if not x["retired"] and x["id"] not in
               {s["id"] for s in SEEDS}]
    # A seed's "enforce" is stored as a learned row carrying only that flag.
    return seeds + learned


def set_flag(lesson_id: str, *, enforce: bool | None = None, retired: bool | None = None,
             by: str = "admin", what: str = "") -> dict:
    """An admin's switch on one lesson (Control → Analysis), recorded."""
    doc = load()
    seed = next((s for s in SEEDS if s["id"] == lesson_id), None)
    if seed is not None:
        if retired is not None:
            rs = set(doc["retired_seeds"])
            rs = rs | {lesson_id} if retired else rs - {lesson_id}
            doc["retired_seeds"] = sorted(rs)
        if enforce is not None:
            row = next((x for x in doc["lessons"] if x["id"] == lesson_id), None)
            if row is None:
                row = {k: seed[k] for k in ("id", "rule", "kind", "topic")} | {
                    "retired": True, "by": "seed"}       # a flag holder, never shown
                doc["lessons"].append(row)
            row["enforce"] = enforce
    else:
        row = next((x for x in doc["lessons"] if x["id"] == lesson_id), None)
        if row is None:
            return doc
        if enforce is not None:
            row["enforce"] = enforce
        if retired is not None:
            row["retired"] = retired
    flags = [f"{k} {'on' if v else 'off'}" for k, v in
             (("enforce", enforce), ("retired", retired)) if v is not None]
    note(doc, by, what or f"{lesson_id}: {', '.join(flags)}")
    return save(**doc)


# ── matching ─────────────────────────────────────────────────────────────

def finding_text(f: dict) -> str:
    m = f.get("metric") or {}
    return _norm(" ".join(str(x) for x in (f.get("id", ""), f.get("claim", ""),
                                           m.get("name", ""), f.get("subsystem") or "")))


def about(lesson: dict, f: dict) -> bool:
    """Is finding `f` about this lesson's topic? Any topic word in its text.
    "withinnormal" stands for the normal-reading family (`_NORMAL`)."""
    text = finding_text(f)
    for t in lesson["topic"]:
        if t == "withinnormal":
            if f.get("severity") in ("ok", "idle") and _NORMAL.search(text):
                return True
        elif t and t in text:
            return True
    return False


# What a "nothing to see" finding says, normalised (no spaces).
_NORMAL = re.compile(r"withinnormal|withinlimits|withinrange|nofault|noanomal|noissue|"
                     r"no(?:brownout|peak|significant|latched)|normal|healthy|allclear|nominal")


# ── 2. load ──────────────────────────────────────────────────────────────

def select(lessons: list[dict], data_text: str, *, limit: int = SELECT_LIMIT,
           max_chars: int = SELECT_CHARS) -> list[dict]:
    """
    The lessons for this run: those whose topic appears in what the log's data
    says (fault names, the detector's kinds, signals), `avoid` before `keep`,
    the rest only while there's room. Progressive disclosure: the model never
    reads the whole book.
    """
    hay = _norm(data_text)

    def hit(x: dict) -> bool:
        return any(t == "withinnormal" or t in hay for t in x["topic"])

    ranked = sorted(lessons, key=lambda x: (not hit(x), x["kind"] != "avoid",
                                            not x.get("seed")))
    out, used = [], 0
    for x in ranked:
        if len(out) >= limit or not hit(x):
            break
        if used + len(x["rule"]) > max_chars:
            continue
        out.append(x)
        used += len(x["rule"])
    return out


def as_prompt(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    lines = [f"- {'Avoid' if x['kind'] == 'avoid' else 'Do'}: {x['rule']}" for x in lessons]
    return ("This crew's playbook (learned from how they rated earlier findings; follow "
            "it):\n" + "\n".join(lines) + "\n\n")


# ── 4. enforce ───────────────────────────────────────────────────────────

def enforce(insights: dict, lessons: list[dict], protect=None) -> list[dict]:
    """
    Drop the findings an enforced `avoid` lesson is about. Never a `fault`,
    never a detector finding (`detector_…`), never one a `keep` lesson is
    about, never the last finding; only
    drops, never a figure. Returns [{finding, lesson}] for the run's stats.
    """
    rules = [x for x in lessons if x["kind"] == "avoid" and x.get("enforce")]
    keeps = [x for x in lessons if x["kind"] == "keep"]
    findings = list(insights.get("findings") or [])
    dropped: list[dict] = []
    for f in list(findings):
        if len(findings) <= 1:
            break
        if f.get("severity") == "fault" or str(f.get("id", "")).startswith("detector_"):
            continue
        if any(about(k, f) for k in keeps):      # a keep lesson wants it: never dropped
            continue
        if protect is not None and protect(f):   # it carries a detector item: never dropped
            continue
        hit = next((x for x in rules if about(x, f)), None)
        if hit is not None:
            findings.remove(f)
            dropped.append({"finding": f.get("id"), "lesson": hit["id"]})
    if dropped:
        insights["findings"] = findings
    return dropped


def drop_filler(insights: dict) -> list[str]:
    """
    At most one finding that says nothing needs doing, and none beside a warn
    or a fault. Measured on every rated run (2026-11-03): of the ok/idle
    findings this drops, 94 of 94 were rated not useful; the single all-clear
    it keeps is what the crew called useful. Never leaves no findings.
    """
    findings = [f for f in insights.get("findings") or [] if isinstance(f, dict)]
    serious = any(f.get("severity") in ("warn", "fault") for f in findings)
    kept, dropped, calm = [], [], 0
    for f in findings:
        if f.get("severity") in ("ok", "idle"):
            calm += 1
            if serious or calm > 1:
                dropped.append(str(f.get("id")))
                continue
        kept.append(f)
    if dropped and kept:
        insights["findings"] = kept
        return dropped
    return []


# ── 3. measure ───────────────────────────────────────────────────────────

@dataclass
class Record:
    """A lesson's track record from the crew's verdicts."""
    given: int = 0                                    # runs that were given it
    after: dict = field(default_factory=lambda: {"useful": 0, "not_useful": 0, "wrong": 0})
    before: dict = field(default_factory=lambda: {"useful": 0, "not_useful": 0, "wrong": 0})

    @staticmethod
    def _bad(d: dict) -> tuple[int, float | None]:
        n = sum(d.values())
        return n, ((d["not_useful"] + d["wrong"]) / n if n else None)

    @property
    def ignored(self) -> bool:
        """Given, and the findings it's about are still rated not useful."""
        n, bad = self._bad(self.after)
        return n >= ENFORCE_MIN_RATED and bad is not None and bad >= ENFORCE_MIN_SHARE

    def summary(self, kind: str) -> str:
        n_a, bad_a = self._bad(self.after)
        n_b, bad_b = self._bad(self.before)
        if not self.given:
            return "not given to a run yet"
        out = f"given to {self.given} run(s)"
        if n_a:
            good = self.after["useful"]
            out += f" · {good} of {n_a} findings on it rated useful since"
        else:
            out += " · no findings on it rated since"
        if n_b and kind == "avoid":
            out += f" (before: {self.before['useful']} of {n_b})"
        return out


def evaluate(conn=None, lessons: list[dict] | None = None) -> dict[str, Record]:
    """
    Each lesson's record, from `analysis_run.stats.playbook` (which lessons a
    run was given) and `analysis_feedback`. Same on every machine: both sync.
    """
    if conn is None:
        from app.ai import tools
        conn = tools.connection()
    lessons = lessons if lessons is not None else active()
    rec = {x["id"]: Record() for x in lessons}
    ratings: dict[int, dict[str, str]] = {}
    for r in conn.fetchall("SELECT run_id, finding_id, rating FROM analysis_feedback "
                           "WHERE finding_id <> '' AND rating IS NOT NULL"):
        ratings.setdefault(r["run_id"], {})[r["finding_id"]] = r["rating"]
    for r in conn.fetchall("SELECT id, insights, stats FROM analysis_run "
                           "WHERE status IN ('published', 'rejected')"):
        try:
            stats = json.loads(r["stats"] or "{}") or {}
            findings = (json.loads(r["insights"] or "{}") or {}).get("findings") or []
        except ValueError:
            continue
        given = set(stats.get("playbook") or [])
        rated = ratings.get(r["id"], {})
        for x in lessons:
            if x["id"] in given:
                rec[x["id"]].given += 1
            for f in findings:
                verdict = rated.get(f.get("id")) if isinstance(f, dict) else None
                if verdict in ("useful", "not_useful", "wrong") and about(x, f):
                    side = rec[x["id"]].after if x["id"] in given else rec[x["id"]].before
                    side[verdict] += 1
    return rec


def auto_enforce(conn=None, by: str = "code") -> list[str]:
    """
    Turn on enforcement for `avoid` lessons the model keeps ignoring, when
    they stand on enough verdicts. Returns the ids switched on (and saves).
    """
    doc = load()
    lessons = active(doc)
    records = evaluate(conn, lessons)
    on = []
    for x in lessons:
        if x["kind"] != "avoid" or x.get("enforce"):
            continue
        sources = ENFORCE_MIN_SOURCES if x.get("seed") else len(x.get("learned_from") or [])
        r = records.get(x["id"])
        if r is not None and r.ignored and sources >= ENFORCE_MIN_SOURCES:
            on.append(x["id"])
    for lid in on:
        r = records[lid]
        set_flag(lid, enforce=True, by=by, what=(
            f"{lid}: enforced by code: the model was given it and the crew still rated "
            f"{r.after['not_useful'] + r.after['wrong']} of {sum(r.after.values())} "
            f"findings on it not useful"))
    return on
