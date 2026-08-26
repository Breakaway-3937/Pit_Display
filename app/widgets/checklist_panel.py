"""
Checklist editor for the control screen.

Drives what one presentation screen shows in Checklist content mode: which list
it points at, what is on that list, and what is ticked.

## Why the ticking lives here

The overhead screens are audience-facing and out of reach — nobody is walking
over to a monitor above the workbench to tap an item. Every tick, and every
edit, happens on the operator's panel and travels to the display through the
`checklist` singleton's signals, the same way every other cross-window command
in this app does.

## Not admin-gated

Writing items and ticking them is data entry, exactly like naming a CAN id, and
the crew needs it mid-match-cycle without hunting for a password. **Deleting a
whole list is gated**, because that destroys every item on it — the same line
this app draws for deleting an imported log session.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QSizePolicy, QVBoxLayout, QWidget,
)

from app import brand
from app.admin import admin
from app.checklist import checklist
from app.config import config
from app.widgets.brand_widgets import RoundedButton, mono_font
from app.widgets.helpers import clear_layout, label


class _EditableRow(QFrame):
    """One item as the operator sees it: tick, text, reorder, remove."""

    def __init__(self, item, accent: str, parent=None):
        super().__init__(parent)
        self.item_id = item.id
        self.setObjectName("checklist_row")
        self.setFixedHeight(44)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 4, 4)
        row.setSpacing(8)

        # Secondary, not ghost: an unticked item still needs a visible box to
        # aim at. A ghost button with no text is invisible until you hover it,
        # which is no good when you are ticking items with gloves on.
        self._tick = RoundedButton("✓" if item.done else "", variant="secondary")
        self._tick.setFixedSize(30, 30)
        self._tick.setToolTip("Tick this item off")
        self._tick.clicked.connect(lambda: checklist.toggle(self.item_id))
        row.addWidget(self._tick)

        self._text = QLabel(item.text)
        self._text.setWordWrap(True)
        row.addWidget(self._text, stretch=1)

        for glyph, delta, tip in (("↑", -1, "Move up"), ("↓", +1, "Move down")):
            b = RoundedButton(glyph, variant="ghost")
            b.setFixedSize(28, 28)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, d=delta: checklist.move_item(self.item_id, d))
            row.addWidget(b)

        edit = RoundedButton("✎", variant="ghost")
        edit.setFixedSize(28, 28)
        edit.setToolTip("Rename this item")
        edit.clicked.connect(self._rename)
        row.addWidget(edit)

        rm = RoundedButton("✕", variant="ghost")
        rm.setFixedSize(28, 28)
        rm.setToolTip("Remove this item")
        rm.clicked.connect(lambda: checklist.delete_item(self.item_id))
        row.addWidget(rm)

        self.apply_state(item.done, accent)

    def apply_state(self, done: bool, _accent: str):
        # Green, not the team accent — the overhead display says done in
        # STATUS_ONLINE and the operator's copy has to agree at a glance.
        self._tick.setText("✓" if done else "")
        self._tick.set_accent(brand.STATUS_ONLINE if done else brand.FAINT_DARK)
        self._tick.set_active(done)
        self.setStyleSheet(
            f"QFrame#checklist_row {{ background: {brand.CARBON_SURF};"
            f" border: 1px solid {brand.CARBON_LINE};"
            f" border-radius: {brand.R_BTN}px; }}"
        )
        color = brand.FAINT_DARK if done else brand.INK_DARK
        f = self._text.font()
        f.setStrikeOut(done)
        self._text.setFont(f)
        self._text.setStyleSheet(f"color: {color}; background: transparent;")

    def _rename(self):
        text, ok = QInputDialog.getText(self, "Rename item", "Item text:",
                                        text=self._text.text())
        if ok:
            checklist.edit_item(self.item_id, text)


class ChecklistPanel(QWidget):
    """Editor for the checklist shown on `screen_id`."""

    def __init__(self, screen_id: str, parent=None):
        super().__init__(parent)
        self._screen_id = screen_id
        self._rows: list[_EditableRow] = []
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        self._build_ui()

        checklist.lists_changed.connect(self._reload_lists)
        checklist.items_changed.connect(self._on_items_changed)
        checklist.item_toggled.connect(self._on_item_toggled)
        config.team_changed.connect(self._on_team_changed)
        admin.lock_state_changed.connect(self._apply_lock)

        self._reload_lists()
        self._apply_lock(admin.unlocked)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        accent = config.active_team.primary_color
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # Which list this screen shows
        pick = QHBoxLayout()
        pick.setContentsMargins(0, 0, 0, 0)
        pick.setSpacing(8)
        self._combo = QComboBox()
        self._combo.currentIndexChanged.connect(self._on_list_selected)
        pick.addWidget(self._combo, stretch=1)

        self._new_btn = RoundedButton("New list", variant="secondary", accent=accent)
        self._new_btn.clicked.connect(self._new_list)
        pick.addWidget(self._new_btn)

        self._rename_btn = RoundedButton("Rename", variant="ghost")
        self._rename_btn.clicked.connect(self._rename_list)
        pick.addWidget(self._rename_btn)

        self._delete_btn = RoundedButton("Delete list", variant="ghost")
        self._delete_btn.clicked.connect(self._delete_list)
        pick.addWidget(self._delete_btn)
        outer.addLayout(pick)

        # Progress + reset
        status = QHBoxLayout()
        status.setContentsMargins(0, 0, 0, 0)
        status.setSpacing(8)
        self._progress = label("", "stat_value")
        self._progress.setFont(mono_font(13))
        status.addWidget(self._progress)
        status.addStretch()
        self._reset_btn = RoundedButton("Reset all", variant="secondary", accent=accent)
        self._reset_btn.setToolTip("Un-tick every item — the between-matches action")
        self._reset_btn.clicked.connect(self._reset)
        status.addWidget(self._reset_btn)
        outer.addLayout(status)

        # Items
        self._items_host = QWidget()
        self._items = QVBoxLayout(self._items_host)
        self._items.setContentsMargins(0, 0, 0, 0)
        self._items.setSpacing(6)
        outer.addWidget(self._items_host)

        self._empty = label("", "stat_label")
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        # Add
        add = QHBoxLayout()
        add.setContentsMargins(0, 0, 0, 0)
        add.setSpacing(8)
        self._entry = QLineEdit()
        self._entry.setPlaceholderText("Add an item…")
        self._entry.returnPressed.connect(self._add)
        add.addWidget(self._entry, stretch=1)
        self._add_btn = RoundedButton("Add", variant="primary", accent=accent)
        self._add_btn.clicked.connect(self._add)
        add.addWidget(self._add_btn)
        outer.addLayout(add)

    # ── List selection ────────────────────────────────────────────────────

    def _current_list_id(self) -> int | None:
        data = self._combo.currentData()
        return int(data) if data is not None else None

    def _reload_lists(self):
        chosen = config.get(self._screen_id, "checklist_id") or checklist.default_list_id()
        self._combo.blockSignals(True)
        self._combo.clear()
        for cl in checklist.lists():
            self._combo.addItem(cl.name, cl.id)
        index = self._combo.findData(int(chosen)) if chosen else -1
        self._combo.setCurrentIndex(index if index >= 0 else 0)
        self._combo.blockSignals(False)
        self._publish()
        self._rebuild()

    def _on_list_selected(self, _index: int):
        self._publish()
        self._rebuild()

    def _publish(self):
        """Tell this screen which list to show. Same route as every other command."""
        list_id = self._current_list_id()
        if list_id is not None:
            config.set(self._screen_id, "checklist_id", list_id)

    # ── Items ─────────────────────────────────────────────────────────────

    def _rebuild(self):
        clear_layout(self._items)
        self._rows = []
        list_id = self._current_list_id()
        items = checklist.items(list_id) if list_id else []
        accent = config.active_team.primary_color
        for item in items:
            row = _EditableRow(item, accent)
            self._items.addWidget(row)
            self._rows.append(row)
        # Soak up the leftover height here. Without it QVBoxLayout hands the
        # spare space to the rows' cells, and fixed-height rows just float
        # apart in the middle of them.
        self._items.addStretch()
        self._empty.setVisible(not items)
        self._empty.setText(
            "No items yet. Type one below — it appears on the screen as soon "
            "as you add it."
        )
        self._refresh_progress()

    def _on_items_changed(self, list_id: int):
        if list_id == self._current_list_id():
            self._rebuild()

    def _on_item_toggled(self, list_id: int, item_id: int, done: bool):
        if list_id != self._current_list_id():
            return
        accent = config.active_team.primary_color
        for row in self._rows:
            if row.item_id == item_id:
                row.apply_state(done, accent)
                break
        self._refresh_progress()

    def _refresh_progress(self):
        list_id = self._current_list_id()
        done, total = checklist.progress(list_id) if list_id else (0, 0)
        self._progress.setText(f"{done} / {total} done" if total else "No items")
        self._reset_btn.setEnabled(bool(done))

    # ── Actions ───────────────────────────────────────────────────────────

    def _add(self):
        list_id = self._current_list_id()
        if list_id is None:
            return
        if checklist.add_item(list_id, self._entry.text()):
            self._entry.clear()

    def _reset(self):
        list_id = self._current_list_id()
        if list_id is not None:
            checklist.reset(list_id)

    def _new_list(self):
        name, ok = QInputDialog.getText(self, "New checklist", "Name:")
        if not ok:
            return
        new_id = checklist.create_list(name)
        if new_id:
            index = self._combo.findData(new_id)
            if index >= 0:
                self._combo.setCurrentIndex(index)

    def _rename_list(self):
        list_id = self._current_list_id()
        if list_id is None:
            return
        name, ok = QInputDialog.getText(self, "Rename checklist", "Name:",
                                        text=self._combo.currentText())
        if ok:
            checklist.rename_list(list_id, name)

    def _delete_list(self):
        list_id = self._current_list_id()
        if list_id is None:
            return
        done, total = checklist.progress(list_id)
        if QMessageBox.question(
            self, "Delete checklist",
            f"Delete “{self._combo.currentText()}” and its {total} item(s)?\n"
            f"This cannot be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return
        checklist.delete_list(list_id)

    # ── Team / lock ───────────────────────────────────────────────────────

    def _on_team_changed(self, team):
        accent = team.primary_color
        for btn in (self._new_btn, self._reset_btn, self._add_btn):
            btn.set_accent(accent)
        self._rebuild()

    def _apply_lock(self, unlocked: bool):
        # Only destruction is gated — writing and ticking stay open. Hidden,
        # not disabled: a greyed-out control invites a hunt for the password.
        self._delete_btn.setVisible(unlocked)
