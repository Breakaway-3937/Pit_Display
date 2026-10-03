"""
Screen Wording (Pit Systems, admin): edit what the pit-front panel and the
words-only overhead slides say, without a code change.

Every editable piece of text is a field in `app/wording.py`, registered by the
screen that shows it. Pick a part of a screen, edit, Save: the screens change
at once, and the edit travels to every pit as the team setting `wording`.
"Shipped text" puts a field back to what the build came with (an emptied
field does the same). Each field has a length limit that keeps it inside its
layout; Save refuses while one is over.

Hidden, not disabled, while the admin lock is closed.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget,
)

from app import wording
from app.admin import admin
from app.widgets.brand_widgets import RoundedButton
from app.widgets.helpers import clear_layout, label


class _FieldRow(QWidget):
    """One field: its label, the editor, a character count, and a reset."""

    def __init__(self, f: wording.Field, parent=None):
        super().__init__(parent)
        self.field = f
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        name = label(f.label, "stat_label")
        name.setWordWrap(True)
        head.addWidget(name, stretch=1)
        self._count = label("", "stat_label")
        head.addWidget(self._count)
        lay.addLayout(head)

        current = wording.text(f.key)
        if f.multiline:
            self.editor = QPlainTextEdit(current)
            # Fixed, or the editor takes every spare pixel of the panel.
            self.editor.setFixedHeight(200 if f.max_len > 400 else 96)
            self.editor.textChanged.connect(self._update_count)
        else:
            self.editor = QLineEdit(current)
            self.editor.setMaxLength(f.max_len)
            self.editor.setMinimumHeight(46)
            self.editor.textChanged.connect(self._update_count)
        lay.addWidget(self.editor)

        foot = QHBoxLayout()
        self._shipped = label("", "stat_label")
        self._shipped.setWordWrap(True)
        foot.addWidget(self._shipped, stretch=1)
        self._reset = RoundedButton("Shipped text", variant="secondary")
        self._reset.setMinimumHeight(46)
        self._reset.clicked.connect(self._on_reset)
        foot.addWidget(self._reset)
        lay.addLayout(foot)
        self._update_count()

    def value(self) -> str:
        if isinstance(self.editor, QPlainTextEdit):
            return self.editor.toPlainText()
        return self.editor.text()

    def over(self) -> int:
        return max(0, len(self.value().strip()) - self.field.max_len)

    def _on_reset(self) -> None:
        if isinstance(self.editor, QPlainTextEdit):
            self.editor.setPlainText(self.field.default)
        else:
            self.editor.setText(self.field.default)

    def _update_count(self) -> None:
        n = len(self.value().strip())
        over = n - self.field.max_len
        self._count.setText(f"{n} / {self.field.max_len}"
                            + (f"  ·  {over} over" if over > 0 else ""))
        shipped = self.value().strip() in ("", self.field.default)
        self._shipped.setText("Shipped text" if shipped else "Edited")
        self._reset.setVisible(not shipped)


class WordingPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        pick = QHBoxLayout()
        pick.addWidget(label("Part of the screen", "stat_label"))
        self._groups = QComboBox()
        self._groups.setMinimumHeight(46)
        self._groups.currentIndexChanged.connect(self._show_group)
        pick.addWidget(self._groups, stretch=1)
        body.addLayout(pick)

        self._rows_host = QWidget()
        self._rows = QVBoxLayout(self._rows_host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(22)
        body.addWidget(self._rows_host)

        actions = QHBoxLayout()
        self._status = label("", "stat_label")
        self._status.setWordWrap(True)
        actions.addWidget(self._status, stretch=1)
        self._save = RoundedButton("Save to every screen", variant="primary")
        self._save.setMinimumHeight(46)
        self._save.clicked.connect(self._on_save)
        actions.addWidget(self._save)
        body.addLayout(actions)
        root.addWidget(self._body)

        self._locked = label("Screen wording is admin-only: unlock with the Breakaway "
                             "mark, top left.", "stat_label")
        self._locked.setWordWrap(True)
        root.addWidget(self._locked)

        self._field_rows: list[_FieldRow] = []
        self._fill_groups()
        admin.lock_state_changed.connect(self._apply_lock)
        # Bound method (the panel can be destroyed): an edit synced in from
        # another pit refreshes what's shown.
        from app.config import config
        config.wording_changed.connect(self._on_wording_changed)
        self._apply_lock(admin.unlocked)

    def _apply_lock(self, unlocked: bool) -> None:
        self._body.setVisible(unlocked)
        self._locked.setVisible(not unlocked)

    def _fill_groups(self) -> None:
        groups: list[str] = []
        for f in wording.fields():
            if f.group not in groups:
                groups.append(f.group)
        self._groups.blockSignals(True)
        self._groups.clear()
        self._groups.addItems(groups)
        self._groups.blockSignals(False)
        self._show_group()

    def _show_group(self, *_args) -> None:
        for row in self._field_rows:
            row.hide()                  # deleted later; never drawn under the new ones
        clear_layout(self._rows)
        self._field_rows = []
        group = self._groups.currentText()
        for f in wording.fields():
            if f.group == group:
                row = _FieldRow(f)
                self._field_rows.append(row)
                self._rows.addWidget(row)
        self._status.setText("")

    def _on_save(self) -> None:
        over = [r.field.label for r in self._field_rows if r.over()]
        if over:
            self._status.setText("Too long for its place on screen: " + ", ".join(over)
                                 + ". Shorten it and save again.")
            return
        wording.set_texts({r.field.key: r.value() for r in self._field_rows})
        self._status.setText("Saved. The screens show it now; other pits get it "
                             "with team sync.")

    def _on_wording_changed(self) -> None:
        # Our own save lands here too; rebuilding shows the stored result.
        status = self._status.text()
        self._show_group()
        self._status.setText(status)
