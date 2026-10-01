"""
The pit's ends of the pipeline: tools over this database, runs and boards into it.

**Board uids are namespaced by machine** (`<session_uid>:pit-<machine>-<run>`).
Home's are `<session_uid>:<analysis.run id>`; a pit counting its own runs from
1 would otherwise write the same uid and replace home's board on every pit.

`analysis_run` is this machine's log of every run, rejected and failed ones
included (DATABASE.md). It doesn't sync yet, and boards made here stay here;
see DATABASE.md "Not built yet".
"""

from __future__ import annotations

import json
import sqlite3

from app.ai import tools
from app.db import db


class ThreadDB:
    """
    A connection of its own for a run on a worker thread: the same database
    and samples file as `db`, the same read helpers. Writes are two short
    transactions per run (start, finish) plus the board.
    """

    def __init__(self, path=None, samples_path=None):
        self._conn = sqlite3.connect(str(path or db.path), timeout=30.0,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("ATTACH DATABASE ? AS samples",
                           (str(samples_path or db.samples_path),))

    def fetchone(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql, params).fetchall()

    def transaction(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()


class LocalToolbox:
    def specs(self) -> list[dict]:
        return tools.SPECS

    def call(self, name: str, arguments: dict) -> dict:
        return tools.call(name, arguments)


class SqliteSink:
    def __init__(self, machine_id: str | None = None):
        if machine_id is None:
            from app.db.sync import settings
            machine_id = settings.load()["machine_id"]
        self.machine_id = machine_id

    def start_run(self, session_uids: list[str], question: str, analyst: str) -> int:
        with tools.connection().transaction() as conn:
            cur = conn.execute(
                """INSERT INTO analysis_run (session_uids, question, analyst, status)
                   VALUES (?, ?, ?, 'running')""",
                (json.dumps(session_uids), question, analyst))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, **fields) -> None:
        cols = ("status", "designer", "reject_reason", "insights", "board", "board_uid",
                "transcript", "stats")
        values = {k: fields.get(k) for k in cols}
        for k in ("insights", "board", "transcript", "stats"):
            if values[k] is not None:
                values[k] = json.dumps(values[k], ensure_ascii=False)
        with tools.connection().transaction() as conn:
            conn.execute(
                f"""UPDATE analysis_run SET {', '.join(f'{c} = ?' for c in cols)},
                      finished_at = datetime('now') WHERE id = ?""",
                (*values.values(), run_id))

    def board_uid(self, session_uids: list[str], run_id: int) -> str:
        head = session_uids[0] if len(session_uids) == 1 else "multi"
        return f"{head}:{self.machine_id}-{run_id}"

    def publish(self, uid: str, board: dict) -> None:
        with tools.connection().transaction() as conn:
            conn.execute(
                """INSERT INTO analysis_board (uid, title, spec, session_uid)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (uid) DO UPDATE SET title = excluded.title,
                     spec = excluded.spec, session_uid = excluded.session_uid,
                     created_at = datetime('now')""",
                (uid, str(board.get("title", ""))[:200], json.dumps(board, ensure_ascii=False),
                 board.get("session_uid")))
