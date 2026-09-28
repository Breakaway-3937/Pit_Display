"""
What syncs, column by column, and how a pulled row lands in SQLite.

**Three scopes** (DATABASE.md, "Sync"):

* **Team-owned**: the rows in `SPECS`, plus the team's settings documents and
  files (`docs.py`). The hub orders them; every machine converges.
* **Machine-produced**: robot log sessions. The row carries the hand-edited
  fields; the data itself travels as a bundle (`bundle.py`).
* **Machine-local**: never synced. The music library (`tracks`: paths on
  this disk), checklist ticks (`done`: this pit's live state), `config`'s
  per-screen settings, `webcast.json` (this pit's cabling), the updater's
  channel, the LED serial port, the secret folder.

On the wire a row is `{column: value}` with the local integer id left out
and every reference replaced by the parent's `uid`, because integer ids
differ between machines. Adding a table: a `Spec` here, and triggers for it in
a new migration (copy `_v10_sync`'s).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    name: str
    cols: tuple[str, ...]
    # A UNIQUE key other than uid. A pulled row that clashes with a local row
    # on it *is* that row: the local one adopts the pulled uid.
    natural: tuple[str, ...] = ()
    # (local fk column, parent table, key in the payload holding the parent's uid)
    parent: tuple[str, str, str] | None = None
    # Columns an update may change, when not all of `cols`.
    update_cols: tuple[str, ...] | None = None
    # Text naturals compare ignoring case, as the panels do (eq.find_preset()).
    nocase: bool = True


# Parents before children: push and apply both walk this order.
SPECS: tuple[Spec, ...] = (
    Spec("led_presets", ("name", "mode", "speed", "brightness", "color"), natural=("name",)),
    Spec("eq_presets", ("name", "preamp", "gains", "built_in"), natural=("name",)),
    Spec("playlists", ("name", "app_mode"), natural=("name",)),
    Spec("playlist_items", ("position",), parent=("playlist_id", "playlists", "playlist_uid")),
    Spec("checklist", ("name", "position"), natural=("name",)),
    Spec("checklist_item", ("text", "position"),
         parent=("checklist_id", "checklist", "checklist_uid")),
    Spec("admin_credential", ("algo", "iterations", "salt", "hash", "is_default")),
    Spec("device", ("device_type", "can_id", "label", "subsystem", "notes"),
         natural=("device_type", "can_id"), update_cols=("label", "subsystem", "notes"),
         nocase=False),
)
BY_NAME = {s.name: s for s in SPECS}

# log_session is synced too, but through the engine: its data needs a bundle.
LOG_SESSION = "log_session"
SESSION_COLS = ("source_name", "source_kind", "device_serial", "started_at",
                "duration_s", "raw_rows", "stored_rows", "source_bytes",
                "match_key", "notes", "keep", "imported_at")
SESSION_EDITABLE = ("match_key", "notes", "keep")

# Written by the home pipeline, read-only on a pit.
ANALYSIS_BOARD = "analysis_board"

# Push order for everything the outbox can hold.
ORDER = [s.name for s in SPECS] + [LOG_SESSION, "setting", "file"]


def order_key(tbl: str) -> int:
    return ORDER.index(tbl) if tbl in ORDER else len(ORDER)


class Pending(Exception):
    """A pulled row that can't land yet; `reason` says what it's waiting for."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _track_key(conn: sqlite3.Connection, track_id: int) -> str | None:
    """A track as every machine can name it: artist|title, lower case."""
    row = conn.execute("SELECT artist, title FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return f"{row[0].strip().lower()}|{row[1].strip().lower()}" if row else None


def serialize(conn: sqlite3.Connection, spec: Spec, uid: str) -> dict | None:
    """The row as the wire carries it, or None when it no longer exists."""
    row = conn.execute(f"SELECT * FROM {spec.name} WHERE uid = ?", (uid,)).fetchone()
    if row is None:
        return None
    row = dict(row)
    data = {c: row.get(c) for c in spec.cols}
    if spec.parent:
        col, parent, key = spec.parent
        p = conn.execute(f"SELECT uid FROM {parent} WHERE id = ?", (row[col],)).fetchone()
        data[key] = p[0] if p else None
    if spec.name == "playlist_items":
        data["track_key"] = _track_key(conn, row["track_id"])
    return data


def apply_upsert(conn: sqlite3.Connection, spec: Spec, uid: str, data: dict) -> str | None:
    """
    Land a pulled row. Returns a uid this machine held for the same thing
    under another identity (its tombstone should go to the hub), or None.
    Raises `Pending` when a parent or track isn't here yet. Call with the
    guard up.
    """
    values = {c: data[c] for c in spec.cols if c in data}
    if spec.parent:
        col, parent, key = spec.parent
        p = conn.execute(f"SELECT id FROM {parent} WHERE uid = ?", (data.get(key),)).fetchone()
        if p is None:
            raise Pending(f"waiting for its {parent}")
        values[col] = p[0]
    if spec.name == "playlist_items":
        key = data.get("track_key") or ""
        t = conn.execute(
            "SELECT id FROM tracks WHERE lower(trim(artist)) || '|' || lower(trim(title)) = ? "
            "ORDER BY missing, id LIMIT 1", (key,)).fetchone()
        if t is None:
            raise Pending("track not in this machine's library")
        values["track_id"] = t[0]

    existing = conn.execute(f"SELECT rowid FROM {spec.name} WHERE uid = ?", (uid,)).fetchone()
    replaced: str | None = None
    if existing is None and spec.natural:
        where = " AND ".join(
            f"{c} = ?" + (" COLLATE NOCASE" if spec.nocase else "") for c in spec.natural)
        clash = conn.execute(
            f"SELECT rowid, uid FROM {spec.name} WHERE {where} ORDER BY rowid LIMIT 1",
            tuple(data.get(c) for c in spec.natural)).fetchone()
        if clash is not None:
            existing = clash
            if clash[1] and clash[1] != uid:
                replaced = clash[1]
            conn.execute(f"UPDATE {spec.name} SET uid = ? WHERE rowid = ?", (uid, clash[0]))

    if existing is not None:
        cols = [c for c in (spec.update_cols or tuple(values)) if c in values]
        if spec.parent and spec.parent[0] in values and spec.parent[0] not in cols:
            cols.append(spec.parent[0])
        if cols:
            conn.execute(
                f"UPDATE {spec.name} SET {', '.join(f'{c} = ?' for c in cols)} WHERE uid = ?",
                (*(values[c] for c in cols), uid))
        return replaced

    cols = list(values)
    params = [values[c] for c in cols]
    if spec.name == "admin_credential":
        cols.append("id")
        params.append(1)
        conn.execute("DELETE FROM admin_credential WHERE id = 1")
    conn.execute(
        f"INSERT INTO {spec.name} ({', '.join(cols)}, uid) "
        f"VALUES ({', '.join('?' for _ in cols)}, ?)", (*params, uid))
    return replaced


def apply_delete(conn: sqlite3.Connection, spec: Spec, uid: str) -> None:
    conn.execute(f"DELETE FROM {spec.name} WHERE uid = ?", (uid,))
