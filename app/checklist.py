"""
Pit checklists — the data spine behind the overhead checklist display.

`checklist` is a lazy proxy: safe to import at module level anywhere. Call
`init_checklist()` once in main() after `init_db()`.

## Shape

A **list** is a named set of **items** in an explicit order. The pit runs more
than one — pre-match, end of day, load-out — so a presentation screen points at
one by id (its per-screen `checklist_id` setting) and the two overhead screens
can show different lists at once.

## Where the tick lives

`done` is a column, not memory. The crew ticks items off across a whole match
cycle and the app closing between matches must not silently un-tick the work.
Resetting is an explicit operator action, never a side effect of a restart.

## Ordering

`position` is the display order, not `id`. Rows get reordered, and a row that
moves must not have to be deleted and re-inserted to do it. Positions are
renumbered densely from 0 after every structural change, so a gap or a
duplicate never survives long enough to make the order ambiguous.
"""

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from app.db import db
from app.lazy_proxy import LazyProxy


@dataclass(frozen=True)
class Checklist:
    id: int
    name: str
    position: int


@dataclass(frozen=True)
class ChecklistItem:
    id: int
    checklist_id: int
    text: str
    position: int
    done: bool


class _ChecklistService(QObject):

    # A list was created, renamed, or deleted — pickers rebuild.
    lists_changed = pyqtSignal()

    # The item set of one list changed structurally (add / edit / delete /
    # reorder / reset). Arg: checklist id. Displays rebuild their rows.
    items_changed = pyqtSignal(int)

    # One item was ticked or un-ticked. Args: checklist id, item id, done.
    # Separate from items_changed because this fires constantly while the crew
    # works, and a display can update one row instead of rebuilding every row.
    item_toggled = pyqtSignal(int, int, bool)

    # ── Lists ─────────────────────────────────────────────────────────────

    def lists(self) -> list[Checklist]:
        rows = db.fetchall(
            "SELECT id, name, position FROM checklist ORDER BY position, id"
        )
        return [Checklist(r["id"], r["name"], r["position"]) for r in rows]

    def get_list(self, list_id: int) -> Checklist | None:
        row = db.fetchone(
            "SELECT id, name, position FROM checklist WHERE id = ?", (list_id,)
        )
        return Checklist(row["id"], row["name"], row["position"]) if row else None

    def default_list_id(self) -> int | None:
        """The list a screen shows when it has not been pointed at one."""
        row = db.fetchone("SELECT id FROM checklist ORDER BY position, id LIMIT 1")
        return row["id"] if row else None

    def create_list(self, name: str) -> int | None:
        name = name.strip()
        if not name:
            return None
        row = db.fetchone("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM checklist")
        with db.transaction():
            cur = db.execute(
                "INSERT INTO checklist (name, position) VALUES (?, ?)",
                (name, row["p"]),
            )
        self.lists_changed.emit()
        return cur.lastrowid

    def rename_list(self, list_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            return
        with db.transaction():
            db.execute("UPDATE checklist SET name = ? WHERE id = ?", (name, list_id))
        self.lists_changed.emit()

    def delete_list(self, list_id: int) -> None:
        """Deletes the list and, by cascade, every item on it."""
        with db.transaction():
            db.execute("DELETE FROM checklist WHERE id = ?", (list_id,))
        self.lists_changed.emit()

    # ── Items ─────────────────────────────────────────────────────────────

    def items(self, list_id: int) -> list[ChecklistItem]:
        rows = db.fetchall(
            """SELECT id, checklist_id, text, position, done
                 FROM checklist_item
                WHERE checklist_id = ?
                ORDER BY position, id""",
            (list_id,),
        )
        return [
            ChecklistItem(r["id"], r["checklist_id"], r["text"],
                          r["position"], bool(r["done"]))
            for r in rows
        ]

    def progress(self, list_id: int) -> tuple[int, int]:
        """(done, total) for one list."""
        row = db.fetchone(
            """SELECT COUNT(*) AS total, COALESCE(SUM(done), 0) AS done
                 FROM checklist_item WHERE checklist_id = ?""",
            (list_id,),
        )
        return (int(row["done"]), int(row["total"])) if row else (0, 0)

    def add_item(self, list_id: int, text: str) -> int | None:
        text = text.strip()
        if not text:
            return None
        row = db.fetchone(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p "
            "FROM checklist_item WHERE checklist_id = ?",
            (list_id,),
        )
        with db.transaction():
            cur = db.execute(
                "INSERT INTO checklist_item (checklist_id, text, position) "
                "VALUES (?, ?, ?)",
                (list_id, text, row["p"]),
            )
        self.items_changed.emit(list_id)
        return cur.lastrowid

    def edit_item(self, item_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            return
        list_id = self._list_of(item_id)
        if list_id is None:
            return
        with db.transaction():
            db.execute("UPDATE checklist_item SET text = ? WHERE id = ?",
                       (text, item_id))
        self.items_changed.emit(list_id)

    def delete_item(self, item_id: int) -> None:
        list_id = self._list_of(item_id)
        if list_id is None:
            return
        with db.transaction():
            db.execute("DELETE FROM checklist_item WHERE id = ?", (item_id,))
            self._renumber(list_id)
        self.items_changed.emit(list_id)

    def move_item(self, item_id: int, delta: int) -> None:
        """Move one item up (-1) or down (+1) in its list."""
        list_id = self._list_of(item_id)
        if list_id is None or delta == 0:
            return
        order = [it.id for it in self.items(list_id)]
        i = order.index(item_id)
        j = i + delta
        if not (0 <= j < len(order)):
            return
        order[i], order[j] = order[j], order[i]
        with db.transaction():
            for pos, iid in enumerate(order):
                db.execute("UPDATE checklist_item SET position = ? WHERE id = ?",
                           (pos, iid))
        self.items_changed.emit(list_id)

    # ── Ticking ───────────────────────────────────────────────────────────

    def set_done(self, item_id: int, done: bool) -> None:
        list_id = self._list_of(item_id)
        if list_id is None:
            return
        with db.transaction():
            db.execute(
                "UPDATE checklist_item SET done = ?, "
                "done_at = CASE WHEN ? THEN datetime('now') ELSE NULL END "
                "WHERE id = ?",
                (1 if done else 0, 1 if done else 0, item_id),
            )
        self.item_toggled.emit(list_id, item_id, done)

    def toggle(self, item_id: int) -> None:
        row = db.fetchone("SELECT done FROM checklist_item WHERE id = ?", (item_id,))
        if row is not None:
            self.set_done(item_id, not bool(row["done"]))

    def reset(self, list_id: int) -> None:
        """Un-tick everything on one list — the between-matches action."""
        with db.transaction():
            db.execute(
                "UPDATE checklist_item SET done = 0, done_at = NULL "
                "WHERE checklist_id = ?",
                (list_id,),
            )
        self.items_changed.emit(list_id)

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _list_of(item_id: int) -> int | None:
        row = db.fetchone(
            "SELECT checklist_id FROM checklist_item WHERE id = ?", (item_id,)
        )
        return row["checklist_id"] if row else None

    @staticmethod
    def _renumber(list_id: int) -> None:
        """Close the gap a delete leaves. Caller supplies the transaction."""
        rows = db.fetchall(
            "SELECT id FROM checklist_item WHERE checklist_id = ? "
            "ORDER BY position, id",
            (list_id,),
        )
        for pos, r in enumerate(rows):
            db.execute("UPDATE checklist_item SET position = ? WHERE id = ?",
                       (pos, r["id"]))


checklist: _ChecklistService = LazyProxy("checklist", "init_checklist")  # type: ignore[assignment]


def init_checklist() -> _ChecklistService:
    """Call once in main(), after init_db()."""
    real = _ChecklistService()
    checklist._install(real)
    return real
