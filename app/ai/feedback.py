"""
Runs and the crew's verdict on them, for the control screen (GUI thread, `db`).

The verdict is the reward signal (CLAUDE.md, "Robot-log analysis"): per
finding **useful / not useful / wrong** and **acted on**, and a 1–5 **rank**
for the run. What it is for: choosing models and prompts by what the crew
found useful, not by how many problems a run reported. A clean bill of health
the crew trusted is a useful finding.
"""

from __future__ import annotations

import json

from app.db import db

RATINGS = ("useful", "not_useful", "wrong")
RUN = ""        # finding_id of the whole-run row


def _session_names(uids: list[str]) -> tuple[str, str, str]:
    """(file name, match key, when the log started) of a run's first session."""
    if not uids:
        return "", "", ""
    row = db.fetchone("SELECT source_name, match_key, started_at FROM log_session "
                      "WHERE uid = ?", (uids[0],))
    if row is None:
        return "(not on this machine)", "", ""
    return row["source_name"], row["match_key"] or "", row["started_at"] or ""


def local_time(utc: str | None) -> str:
    """SQLite's UTC `datetime('now')` as this machine's local 'HH:MM'."""
    from datetime import UTC, datetime
    try:
        return datetime.fromisoformat(utc).replace(tzinfo=UTC).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def recent_runs(limit: int = 8) -> list[dict]:
    out = []
    for r in db.fetchall(
            """SELECT id, status, analyst, started_at, finished_at, session_uids,
                      reject_reason, insights, board, stats
               FROM analysis_run ORDER BY id DESC LIMIT ?""", (limit,)):
        uids = json.loads(r["session_uids"] or "[]")
        name, match, log_start = _session_names(uids)
        insights = json.loads(r["insights"]) if r["insights"] else {}
        board = json.loads(r["board"]) if r["board"] else {}
        out.append({
            "id": r["id"], "status": r["status"], "model": r["analyst"],
            "started_at": r["started_at"], "finished_at": r["finished_at"],
            "session_uids": uids, "session_name": name, "match_key": match,
            "log_started": log_start,
            "reason": r["reject_reason"] or "",
            "findings": (insights.get("findings") or []) if isinstance(insights, dict) else [],
            "title": board.get("title", "") if isinstance(board, dict) else "",
            "headline": board.get("headline") if isinstance(board, dict) else None,
            "stats": json.loads(r["stats"]) if r["stats"] else {},
        })
    return out


def run_trace(run_id: int) -> tuple[list, dict[str, str]]:
    """(the run's steps, uid → log name) for "what it looked at" (`trace.py`)."""
    from app.ai import trace
    row = db.fetchone("SELECT transcript, session_uids FROM analysis_run WHERE id = ?",
                      (run_id,))
    if row is None or not row["transcript"]:
        return [], {}
    try:
        transcript = json.loads(row["transcript"])
    except ValueError:
        return [], {}
    steps = trace.steps(transcript)
    uids = set(json.loads(row["session_uids"] or "[]")) | trace.logs_looked_at(transcript)
    names = {}
    for uid in uids:
        r = db.fetchone("SELECT source_name FROM log_session WHERE uid = ?", (uid,))
        names[uid] = r["source_name"] if r else "a log not on this machine"
    return steps, names


def lessons(limit: int = 8) -> list[str]:
    """
    The crew's verdicts on earlier findings, as short lines the analyst reads
    before it starts (pipeline `lessons`): the closest thing this model gets
    to training on what this crew values. Newest first, wrong and not-useful
    ones before useful ones, one line per distinct claim.
    """
    rows = db.fetchall(
        """SELECT f.finding_id, f.rating, f.acted, r.insights
           FROM analysis_feedback f JOIN analysis_run r ON r.id = f.run_id
           WHERE f.finding_id <> '' AND (f.rating IS NOT NULL OR f.acted = 1)
           ORDER BY f.rated_at DESC""")
    order = {"wrong": 0, "not_useful": 1, "useful": 2}
    picked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for r in rows:
        try:
            found = {x.get("id"): x for x in json.loads(r["insights"] or "{}").get("findings") or []}
        except ValueError:
            continue
        claim = (found.get(r["finding_id"]) or {}).get("claim")
        if not claim or claim in seen:
            continue
        seen.add(claim)
        verdict = "acted on (useful)" if r["acted"] else (r["rating"] or "").replace("_", " ")
        picked.append((0 if r["acted"] else order.get(r["rating"], 3), f"{verdict}: {claim}"))
    return [line for _rank, line in sorted(picked, key=lambda x: x[0])[:limit]]


def verdicts(run_id: int) -> dict[str, dict]:
    """finding_id → {rating, acted, score}; the run's own row is under ''."""
    return {r["finding_id"]: {"rating": r["rating"], "acted": bool(r["acted"]),
                              "score": r["score"]}
            for r in db.fetchall("SELECT * FROM analysis_feedback WHERE run_id = ?",
                                 (run_id,))}


def _upsert(run_id: int, finding_id: str, column: str, value) -> None:
    with db.transaction() as conn:
        conn.execute(
            f"""INSERT INTO analysis_feedback (run_id, finding_id, {column}) VALUES (?, ?, ?)
                ON CONFLICT (run_id, finding_id) DO UPDATE SET {column} = excluded.{column},
                  rated_at = datetime('now')""", (run_id, finding_id, value))


def set_rating(run_id: int, finding_id: str, rating: str | None) -> None:
    if rating is not None and rating not in RATINGS:
        raise ValueError(rating)
    _upsert(run_id, finding_id, "rating", rating)


def set_acted(run_id: int, finding_id: str, acted: bool) -> None:
    _upsert(run_id, finding_id, "acted", int(bool(acted)))


def set_score(run_id: int, score: int | None) -> None:
    if score is not None and not 1 <= int(score) <= 5:
        raise ValueError(score)
    _upsert(run_id, RUN, "score", score)


def newest_session() -> str | None:
    """The newest log with data, analysed or not."""
    row = db.fetchone(
        """SELECT ls.uid FROM log_session ls
           WHERE ls.uid IS NOT NULL
             AND EXISTS (SELECT 1 FROM series se WHERE se.session_id = ls.id)
           ORDER BY ls.imported_at DESC, ls.id DESC LIMIT 1""")
    return row["uid"] if row else None


def unanalysed(since_hours: float | None = 24) -> list[str]:
    """Every log with data that no run has looked at, newest first. With
    `since_hours`, only logs imported (or synced in) that recently: a batch
    import queues all of its logs, not months of old ones."""
    where = "" if since_hours is None else \
        f"AND ls.imported_at >= datetime('now', '-{float(since_hours)} hours')"
    rows = db.fetchall(
        f"""SELECT ls.uid FROM log_session ls
            WHERE ls.uid IS NOT NULL
              AND EXISTS (SELECT 1 FROM series se WHERE se.session_id = ls.id)
              {where}
            ORDER BY ls.started_at DESC, ls.imported_at DESC, ls.id DESC""")
    done = set()
    for r in db.fetchall("SELECT session_uids FROM analysis_run"):
        try:
            done.update(json.loads(r["session_uids"] or "[]"))
        except ValueError:
            pass
    return [r["uid"] for r in rows if r["uid"] not in done]


def recorded_with(uid: str, window_s: int = 60) -> list[str]:
    """
    `uid` and every other log with data recorded within `window_s` of it: the
    robot writes three logs per session (the AdvantageKit .wpilog and a .hoot
    per CAN bus), and only together do they say which mechanism a fault is
    on. `uid` first.
    """
    me = db.fetchone("SELECT started_at FROM log_session WHERE uid = ?", (uid,))
    if me is None or not me["started_at"]:
        return [uid]
    rows = db.fetchall(
        """SELECT ls.uid FROM log_session ls
           WHERE ls.uid IS NOT NULL AND ls.uid <> ? AND ls.started_at IS NOT NULL
             AND abs(strftime('%s', ls.started_at) - strftime('%s', ?)) <= ?
             AND EXISTS (SELECT 1 FROM series se WHERE se.session_id = ls.id)
           ORDER BY ls.started_at""", (uid, me["started_at"], window_s))
    # Partners join even if a run already looked at them alone: the group is
    # what tells the analyst which mechanism a fault is on (real logs,
    # 2026-10-05: excluding them left every run looking at one log).
    return [uid] + [r["uid"] for r in rows]


def newest_unanalysed() -> str | None:
    """The newest log with data that no run has looked at yet, if that's the newest."""
    uid = newest_session()
    if uid is None:
        return None
    seen = db.fetchone("SELECT 1 FROM analysis_run WHERE session_uids LIKE ?",
                       (f'%"{uid}"%',))
    return None if seen else uid


def scoreboard() -> list[dict]:
    """
    Each model and prompt version, judged by the crew's verdicts.

    What counts is **useful** (and acted on), not problems found: `useful` and
    `wrong` are shares of the findings anyone rated, `rank` is the mean 1–5.
    `published` is the share of judged runs that survived the checks: a run
    that **failed** (the database or the model engine went away mid-run) says
    nothing about the prompt, so it's counted in `failed` and left out of the
    share; only `published` against `rejected` judges it (home, R15,
    2026-10-05). Rows with nothing rated still show, so a new model is visible
    before it's judged.
    """
    rows: dict[tuple, dict] = {}
    for r in db.fetchall("SELECT id, analyst, status, stats FROM analysis_run "
                         "WHERE status != 'running'"):
        try:
            prompt = int((json.loads(r["stats"] or "{}") or {}).get("prompt_version") or 1)
        except (TypeError, ValueError):
            prompt = 1
        key = (r["analyst"], prompt)
        row = rows.setdefault(key, {"model": r["analyst"], "prompt": prompt, "runs": 0,
                                    "published": 0, "failed": 0, "rated": 0, "useful": 0, "wrong": 0,
                                    "acted": 0, "ranks": [], "ids": []})
        row["runs"] += 1
        row["published"] += r["status"] == "published"
        row["failed"] += r["status"] == "failed"
        row["ids"].append(r["id"])
    for row in rows.values():
        marks = ",".join("?" * len(row["ids"]))
        for f in db.fetchall(f"SELECT finding_id, rating, acted, score FROM analysis_feedback "
                             f"WHERE run_id IN ({marks})", tuple(row["ids"])):
            if f["finding_id"] == RUN:
                if f["score"]:
                    row["ranks"].append(f["score"])
                continue
            if f["rating"]:
                row["rated"] += 1
                row["useful"] += f["rating"] == "useful"
                row["wrong"] += f["rating"] == "wrong"
            row["acted"] += bool(f["acted"])
    out = []
    for row in rows.values():
        rated = row["rated"]
        judged = row["runs"] - row["failed"]
        out.append({
            "model": row["model"], "prompt": row["prompt"], "runs": row["runs"],
            "published": row["published"] / judged if judged else None,
            "failed": row["failed"],
            "rated": rated,
            "useful": row["useful"] / rated if rated else None,
            "wrong": row["wrong"] / rated if rated else None,
            "acted": row["acted"],
            "rank": sum(row["ranks"]) / len(row["ranks"]) if row["ranks"] else None,
        })
    # Best first: most useful, then fewest wrong, then most runs.
    return sorted(out, key=lambda x: (-(x["useful"] or -1), x["wrong"] or 0, -x["runs"]))
