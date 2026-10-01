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
    `published` is the share of runs that survived the checks. Rows with
    nothing rated still show, so a new model is visible before it's judged.
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
                                    "published": 0, "rated": 0, "useful": 0, "wrong": 0,
                                    "acted": 0, "ranks": [], "ids": []})
        row["runs"] += 1
        row["published"] += r["status"] == "published"
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
        out.append({
            "model": row["model"], "prompt": row["prompt"], "runs": row["runs"],
            "published": row["published"] / row["runs"],
            "rated": rated,
            "useful": row["useful"] / rated if rated else None,
            "wrong": row["wrong"] / rated if rated else None,
            "acted": row["acted"],
            "rank": sum(row["ranks"]) / len(row["ranks"]) if row["ranks"] else None,
        })
    # Best first: most useful, then fewest wrong, then most runs.
    return sorted(out, key=lambda x: (-(x["useful"] or -1), x["wrong"] or 0, -x["runs"]))
