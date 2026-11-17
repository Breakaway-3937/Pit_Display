"""
The curator: the step where the analyst learns (`playbook.py`, step 1).

When enough of the crew's verdicts have arrived since the last pass
(`due()`), and the engine has nothing else to do, the model reads them,
with the crew's reasons, beside the current playbook and proposes at most
four changes: add a lesson, update one, retire one. **The model words;
code decides** (`check_changes`), as everywhere in `app/ai/`:

* a lesson cites the verdicts it's learned from (`from`): two at least, or
  one the crew marked wrong or gave a reason for. `avoid` lessons stand on
  not-useful / wrong verdicts, `keep` lessons on useful / acted-on ones;
* its `topic` (the words a finding about it contains) must appear in what it
  cites, and an `avoid` lesson may not cover a topic the crew has called
  useful, in this batch or **on the record** (the first real pass learned
  "avoid UnlicensedFeatureInUse" from two verdicts against 29 of 31 useful),
  nor contradict a `keep` lesson (retire that first);
* a figure in its rule must be one the crew's cited verdicts wrote;
* verdicts are read **in the order they were rated**, a batch at a time, and
  the watermark (`curated_at`, "rated_at|uid") moves past exactly the batch
  (sorted by priority, the first real pass's watermark skipped ~380);
* it may not restate a lesson that's there (update that one instead); built-in
  seeds are never changed by a model (an admin can retire one).

A change that fails goes back once with the reasons; still failing, only
that change is refused, the rest apply. Then code turns on enforcement for
any `avoid` lesson the model keeps ignoring (`playbook.auto_enforce`). The
watermark (`curated_at`) moves past the verdicts read, so each is learned
from once; every pass is recorded in the playbook's `history` and, whole,
in `curate_log.jsonl` (local, never synced).

No Qt (home can import it). Never raises for a model problem.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from app.ai import checks, playbook

CURATE_VERSION = 1
BATCH = 30                # verdicts per pass
DUE_COUNT = 6             # new verdicts that make a pass worth running
MAX_CHANGES = 4
_GENERIC = {"talonfx", "motor", "motors", "device", "devices", "fault", "faults", "finding",
            "robot", "check", "latched", "session", "log", "issue", "issues", "multiple",
            "high", "low", "the", "and", "single", "warning"}

CURATE_SYSTEM = """\
You keep the playbook for the robot-log analyst of an FRC pit crew. The analyst is a \
small model that reads each robot log and writes findings; the crew rates each finding \
useful, not useful or wrong, marks what they acted on, and sometimes says why. The \
playbook is a few short lessons the analyst reads before every log, so it writes more \
of what the crew finds useful and less of what it doesn't.

Read the new verdicts beside the current playbook and propose 0 to 4 changes:
- add: a new lesson. kind "avoid" (learned from not useful or wrong verdicts) or "keep" \
(learned from useful or acted-on ones). rule: one sentence, under 220 characters, telling \
the analyst what to do or avoid, about a KIND of finding, never one match or one device \
number. topic: 1 to 5 words a finding about it contains (a fault name like BridgeBrownout, \
a signal word like temperature), copied from the verdicts. from: the refs of the verdicts \
it's learned from: at least two, or one marked wrong or with the crew's reason.
- update: change a learned lesson's rule or topic (same id) with new verdicts in from.
- retire: remove a learned lesson when new verdicts contradict it (from: those verdicts).
Rules:
- No figures, unless the crew's own words in a verdict you cite have that number.
- Don't repeat what a lesson already says, and never change one whose id starts with seed_.
- The crew's reason, when there is one, is the most important thing in a verdict.
- Fewer is better. If the verdicts teach nothing new, return no changes.
/no_think"""

RETRY = ("These changes were refused:\n- {problems}\n\nWrite the whole JSON again: fix or "
         "drop each refused change. Keep the changes that weren't refused.")


def format_schema() -> dict:
    return {"type": "object", "required": ["changes"], "properties": {
        "changes": {"type": "array", "maxItems": MAX_CHANGES, "items": {
            "type": "object", "required": ["op", "id", "from"],
            "properties": {
                "op": {"type": "string", "enum": ["add", "update", "retire"]},
                "id": {"type": "string"},
                "kind": {"type": "string", "enum": list(playbook.KINDS)},
                "rule": {"type": "string"},
                "topic": {"type": "array", "maxItems": playbook.MAX_TOPIC,
                          "items": {"type": "string"}},
                "from": {"type": "array", "items": {"type": "string"}}}}}}}


@dataclass
class Result:
    status: str                         # changed | unchanged | failed | nothing
    applied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    enforced: list[str] = field(default_factory=list)
    verdicts: int = 0
    seconds: float = 0.0
    model: str = ""
    error: str = ""
    transcript: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "status", "applied", "refused", "enforced", "verdicts", "seconds", "model",
            "error", "transcript")} | {"version": CURATE_VERSION, "at": time.time()}


# ── the verdicts ─────────────────────────────────────────────────────────

def _mark(v: dict) -> str:
    return f"{v['rated_at']}|{v['uid']}"


def pending(conn=None, since: str | None = None, limit: int = BATCH) -> list[dict]:
    """
    The next batch of verdicts rated after the watermark: the oldest `limit`,
    then shown wrong and reasoned first, each with the finding it judged.
    `ref` is how the model cites one.
    """
    if conn is None:
        from app.ai import tools
        conn = tools.connection()
    since = playbook.load()["curated_at"] if since is None else since
    at, _, after_uid = (since or "").partition("|")
    rows = conn.fetchall(
        """SELECT COALESCE(f.uid, f.run_id || '#' || f.finding_id) AS uid, f.finding_id,
                  f.rating, f.acted, f.note, f.rated_at, r.insights
           FROM analysis_feedback f JOIN analysis_run r ON r.id = f.run_id
           WHERE f.finding_id <> '' AND (f.rating IS NOT NULL OR f.acted = 1)
             AND (f.rated_at > ? OR (f.rated_at = ? AND
                  COALESCE(f.uid, f.run_id || '#' || f.finding_id) > ?))
           ORDER BY f.rated_at, uid""", (at, at, after_uid))
    out, parsed = [], {}
    for r in rows:
        key = r["insights"] or ""
        if key not in parsed:            # one run's insights, parsed once
            try:
                parsed[key] = {x.get("id"): x for x in
                               (json.loads(key or "{}") or {}).get("findings") or []}
            except ValueError:
                parsed[key] = {}
        found = parsed[key]
        f = found.get(r["finding_id"])
        if not isinstance(f, dict):
            continue
        m = f.get("metric") or {}
        out.append({
            "uid": r["uid"], "rated_at": r["rated_at"],
            "rating": "acted on" if r["acted"] and r["rating"] in (None, "useful")
                      else (r["rating"] or "").replace("_", " "),
            "reason": str(r["note"] or "")[:200],
            "claim": str(f.get("claim") or "")[:220], "severity": f.get("severity"),
            "metric": f"{m.get('name', '')}: {m.get('value', '')} {m.get('unit') or ''}".strip(),
            "finding": f})
    batch = out[:limit]
    rank = {"wrong": 0, "not useful": 2, "acted on": 3, "useful": 4}
    batch.sort(key=lambda v: (rank.get(v["rating"], 5) - (1 if v["reason"] else 0)))
    for n, v in enumerate(batch, 1):
        v["ref"] = f"v{n}"
    return batch


def record(conn=None) -> list[tuple[dict, str]]:
    """(finding, rating) for every rated finding on record: what an `avoid`
    lesson is checked against, beyond its own batch."""
    if conn is None:
        from app.ai import tools
        conn = tools.connection()
    out, parsed = [], {}
    for r in conn.fetchall(
            """SELECT f.finding_id, f.rating, f.acted, r.id, r.insights
               FROM analysis_feedback f JOIN analysis_run r ON r.id = f.run_id
               WHERE f.finding_id <> '' AND (f.rating IS NOT NULL OR f.acted = 1)"""):
        if r["id"] not in parsed:
            try:
                parsed[r["id"]] = {x.get("id"): x for x in
                                   (json.loads(r["insights"] or "{}") or {}).get("findings") or []}
            except ValueError:
                parsed[r["id"]] = {}
        f = parsed[r["id"]].get(r["finding_id"])
        if isinstance(f, dict):
            out.append((f, "useful" if r["acted"] else r["rating"]))
    return out


def due(conn=None) -> bool:
    """Enough new verdicts to learn from: `DUE_COUNT`, or any wrong, or two reasons."""
    new = pending(conn, limit=BATCH)
    return (len(new) >= DUE_COUNT or any(v["rating"] == "wrong" for v in new)
            or sum(bool(v["reason"]) for v in new) >= 2)


# ── the checks ───────────────────────────────────────────────────────────

def _good(v: dict) -> bool:
    return v["rating"] in ("useful", "acted on")


def _text(v: dict) -> str:
    return playbook._norm(" ".join([v["claim"], v["metric"], v["reason"],
                                    str(v["finding"].get("id", ""))]))


LIKED_MIN = 2             # useful verdicts on a topic that bar an avoid lesson…
LIKED_SHARE = 0.25        # …when they're at least this share of its rated findings


def check_changes(changes: list, verdicts: list[dict], doc: dict,
                  history: list[tuple[dict, str]] | None = None
                  ) -> tuple[list[dict], list[str]]:
    """(the changes that pass, a problem line per refused one). `history` is
    `record()`: every rated finding, so a lesson can't undo what the crew
    has called useful before."""
    by_ref = {v["ref"]: v for v in verdicts}
    active = playbook.active(doc)
    learned = {x["id"]: x for x in doc["lessons"] if not x["retired"]}
    seeds = {s["id"] for s in playbook.SEEDS}
    n_learned = len([x for x in learned.values() if x["id"] not in seeds])
    ok, problems, taken = [], [], set()
    for i, c in enumerate(changes if isinstance(changes, list) else [], 1):
        if not isinstance(c, dict):
            problems.append(f"change {i}: not an object")
            continue
        op, lid = c.get("op"), str(c.get("id") or "")
        name = f"change {i} ({op} {lid!r})"
        cited = [by_ref[r] for r in c.get("from") or [] if r in by_ref]
        unknown = [r for r in c.get("from") or [] if r not in by_ref]
        if unknown:
            problems.append(f"{name}: no verdict {', '.join(map(str, unknown))}")
            continue
        if op == "add":
            # Code names a new lesson: on all ~420 real verdicts the model
            # copied the seeds' "seed_" prefix for its new ones, and four
            # passes' adds were refused for it (2026-11-03).
            lid = "learned_" + re.sub(r"^(seed|learned)_+", "",
                                      re.sub(r"[^a-z0-9_]+", "_", lid.lower()).strip("_"))
            lid = lid[:48] if lid != "learned_" else f"learned_{i}"
        elif lid in seeds or lid.startswith("seed_"):
            problems.append(f"{name}: built-in lessons can't be changed")
            continue
        if op == "retire":
            x = learned.get(lid)
            if x is None:
                problems.append(f"{name}: no learned lesson {lid!r}")
            elif not any(playbook.about(x, v["finding"]) and _good(v) != (x["kind"] == "keep")
                         for v in cited):
                problems.append(f"{name}: none of the cited verdicts contradicts it")
            else:
                ok.append({"op": "retire", "id": lid, "from": [v["uid"] for v in cited]})
            continue
        if op not in ("add", "update"):
            problems.append(f"{name}: op must be add, update or retire")
            continue
        if op == "update" and lid not in learned:
            problems.append(f"{name}: no learned lesson {lid!r} to update; add it")
            continue
        kind = c.get("kind") or (learned.get(lid) or {}).get("kind")
        rule = " ".join(str(c.get("rule") or (learned.get(lid) or {}).get("rule") or "").split())
        topic = [playbook._norm(t) for t in c.get("topic") or
                 (learned.get(lid) or {}).get("topic") or []]
        topic = [t for t in topic if t]
        if kind not in playbook.KINDS:
            problems.append(f"{name}: kind must be avoid or keep")
            continue
        if not 20 <= len(rule) <= playbook.MAX_RULE:
            problems.append(f"{name}: rule must be 20 to {playbook.MAX_RULE} characters")
            continue
        if not cited:
            problems.append(f"{name}: cite the verdicts it's learned from")
            continue
        wrong_side = [v["ref"] for v in cited if _good(v) != (kind == "keep")]
        if wrong_side:
            problems.append(f"{name}: {'an avoid' if kind == 'avoid' else 'a keep'} lesson can't stand on {', '.join(wrong_side)} "
                            f"(rated {by_ref[wrong_side[0]]['rating']})")
            continue
        strong = [v for v in cited if v["rating"] == "wrong" or v["reason"]]
        if op == "add" and len(cited) < 2 and not strong:
            problems.append(f"{name}: one verdict without a reason isn't a lesson; cite two")
            continue
        if not topic or len(topic) > playbook.MAX_TOPIC:
            problems.append(f"{name}: topic needs 1 to {playbook.MAX_TOPIC} words")
            continue
        vague = [t for t in topic if t in _GENERIC or len(t) < 4]
        if vague:
            problems.append(f"{name}: topic word(s) {', '.join(vague)} would match almost "
                            "every finding; use the fault or signal name")
            continue
        hay = " ".join(_text(v) for v in cited)
        ungrounded = [t for t in topic if t not in hay]
        if ungrounded:
            problems.append(f"{name}: topic {', '.join(ungrounded)} isn't in the verdicts "
                            "it cites")
            continue
        pool = checks.context_numbers(*[" ".join([v["claim"], v["metric"], v["reason"]])
                                        for v in cited])
        invented = [f"{n:g}" for n, places in checks.text_numbers(rule)
                    if not checks.shown_as(n, places, pool)]
        if invented:
            problems.append(f"{name}: figure(s) {', '.join(invented)} aren't in the cited "
                            "verdicts; a lesson has no figures of its own")
            continue
        lesson = {"id": lid, "kind": kind, "rule": rule, "topic": topic}
        if kind == "avoid":
            liked = [v["ref"] for v in verdicts if _good(v) and playbook.about(lesson, v["finding"])]
            if liked:
                problems.append(f"{name}: the crew rated {', '.join(liked[:3])} useful, and "
                                "this lesson would cover it; narrow the topic")
                continue
            on_it = [r for f, r in history or [] if playbook.about(lesson, f)]
            good_n = sum(r == "useful" for r in on_it)
            if good_n >= LIKED_MIN and good_n >= LIKED_SHARE * len(on_it):
                problems.append(f"{name}: the crew rated {good_n} of {len(on_it)} findings on "
                                "this topic useful; narrow the topic or leave it")
                continue
        clash = next((x for x in active if x["id"] != lid and x["kind"] != kind
                      and set(topic) & set(x["topic"])), None)
        if clash is not None:
            problems.append(f"{name}: it contradicts {clash['id']} ({clash['kind']}: "
                            f"{clash['rule'][:80]}); retire that one first, if it's wrong")
            continue
        same = next((x for x in active if x["id"] != lid and x["kind"] == kind
                     and set(topic) <= set(x["topic"])), None)
        if same is not None:
            problems.append(f"{name}: {same['id']} already covers this topic; update it "
                            "instead (built-in ones can't be)")
            continue
        if op == "add":
            if lid in learned or lid in taken or not lid:
                problems.append(f"{name}: id taken; pick a new one or update")
                continue
            if n_learned >= playbook.MAX_LEARNED:
                problems.append(f"{name}: the playbook is full ({playbook.MAX_LEARNED}); "
                                "merge two by updating one and retiring the other")
                continue
            n_learned += 1
            taken.add(lid)
        ok.append(lesson | {"op": op, "from": [v["uid"] for v in cited]})
    return ok, problems


def apply(changes: list[dict], doc: dict, by: str) -> list[str]:
    """Write passed changes into `doc` (in place). Returns one line each."""
    lines = []
    today = time.strftime("%Y-%m-%d")
    for c in changes:
        lid = c["id"]
        row = next((x for x in doc["lessons"] if x["id"] == lid), None)
        if c["op"] == "retire":
            if row is not None:
                row["retired"] = True
                row["learned_from"] = (row.get("learned_from") or []) + c["from"]
            lines.append(f"retired {c['id']}")
            continue
        if row is None:
            row = {"id": lid, "added": today, "by": by, "learned_from": []}
            doc["lessons"].append(row)
        elif row.get("rule") == c["rule"] and row.get("topic") == c["topic"]:
            # The same words again: more evidence, not a change.
            row["learned_from"] = list(dict.fromkeys((row.get("learned_from") or [])
                                                     + c["from"]))
            continue
        row.update({"rule": c["rule"], "kind": c["kind"], "topic": c["topic"],
                    "learned_from": list(dict.fromkeys((row.get("learned_from") or [])
                                                       + c["from"]))})
        lines.append(f"{c['op']} {row['id']}: {c['rule']}")
    return lines


# ── the pass ─────────────────────────────────────────────────────────────

def curate(llm, model: str, *, conn=None, retries: int = 1, by: str = "") -> Result:
    """One pass over the pending verdicts. Saves the playbook; never raises
    for a model problem."""
    t0 = time.monotonic()
    res = Result("nothing", model=model)
    verdicts = pending(conn)
    if not verdicts:
        return res
    res.verdicts = len(verdicts)
    doc = playbook.load()
    records = playbook.evaluate(conn, playbook.active(doc))
    book = [{"id": x["id"], "kind": x["kind"], "rule": x["rule"], "topic": x["topic"],
             "record": records[x["id"]].summary(x["kind"]) if x["id"] in records else ""}
            for x in playbook.active(doc)]
    shown = [{k: v[k] for k in ("ref", "rating", "reason", "claim", "severity", "metric")}
             for v in verdicts]
    msgs = [{"role": "system", "content": CURATE_SYSTEM},
            {"role": "user", "content": json.dumps(
                {"playbook": book, "new_verdicts": shown}, ensure_ascii=False)}]
    res.transcript = msgs
    passed: list[dict] = []
    history = record(conn)
    try:
        for attempt in range(retries + 1):
            reply = llm.chat(model, msgs, format=format_schema(),
                             temperature=0.2 if attempt == 0 else 0.5, max_tokens=1500)
            msgs.append({"role": "assistant", "content": reply.content})
            try:
                out = json.loads(reply.content)
                changes = out.get("changes") if isinstance(out, dict) else None
                problems = [] if isinstance(changes, list) else ["no changes array"]
            except ValueError as e:
                changes, problems = None, [f"not valid JSON: {e}"]
            if isinstance(changes, list):
                passed, problems = check_changes(changes, verdicts, doc, history)
            if not problems:
                break
            if attempt < retries:
                msgs.append({"role": "user", "content":
                             RETRY.format(problems="\n- ".join(problems[:10]))})
        res.refused = problems
        if not isinstance(changes, list):
            raise ValueError(problems[0])     # nothing readable: the verdicts wait
    except Exception as e:                   # the engine went away: try again later
        res.status, res.error = "failed", f"{type(e).__name__}: {e}"
        res.seconds = round(time.monotonic() - t0, 1)
        return res
    by = by or model
    res.applied = apply(passed, doc, by)
    doc["curated_at"] = max(_mark(v) for v in verdicts)
    playbook.note(doc, by, f"learned from {len(verdicts)} verdict(s): "
                           + ("; ".join(res.applied) if res.applied else "nothing new"),
                  refused=len(res.refused), version=CURATE_VERSION)
    playbook.save(**doc)
    res.enforced = playbook.auto_enforce(conn)
    res.status = "changed" if res.applied or res.enforced else "unchanged"
    res.seconds = round(time.monotonic() - t0, 1)
    return res
