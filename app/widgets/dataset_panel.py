"""
Which of home's datasets may go on the overhead screens (admin).

Every dataset home sends (`home_dataset`) is listed with a switch, **off until
an adult has reviewed it** (home/REQUESTS.md R10/R11). The switches write the
team setting `datasets` (`app/dataset_settings.py`), so one review covers
every pit. Hidden, not disabled, while the admin lock is closed.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app import dataset_settings, datasets
from app.admin import admin
from app.widgets.helpers import clear_layout, label
from app.widgets.toggle_switch import ToggleSwitch


class DatasetPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._body = QWidget()
        self._rows = QVBoxLayout(self._body)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(10)
        root.addWidget(self._body)
        self._locked = label("Datasets are admin-only: unlock with the Breakaway mark, "
                             "top left.", "stat_label")
        self._locked.setWordWrap(True)
        root.addWidget(self._locked)
        admin.lock_state_changed.connect(self._apply_lock)
        try:
            from app.db.sync.service import sync
            sync.state_changed.connect(self.rebuild)
        except RuntimeError:
            pass
        self._apply_lock(admin.unlocked)
        self.rebuild()

    def _apply_lock(self, unlocked: bool) -> None:
        self._body.setVisible(unlocked)
        self._locked.setVisible(not unlocked)

    def rebuild(self) -> None:
        clear_layout(self._rows)
        found = datasets.all_datasets(enabled_only=False)
        if not found:
            note = label("Nothing from home yet. Datasets arrive with team sync.", "stat_label")
            note.setWordWrap(True)
            self._rows.addWidget(note)
            return
        on = set(dataset_settings.load()["enabled"])
        for d in found:
            row = QHBoxLayout()
            row.setSpacing(12)
            words = label(d.title + (f"  ·  {d.description}" if d.description else ""),
                          "stat_value")
            words.setWordWrap(True)
            words.setStyleSheet("font-size: 15px;")
            row.addWidget(words, stretch=1)
            switch = ToggleSwitch()
            switch.setChecked(d.key in on)
            switch.setProperty("dataset", d.key)
            switch.toggled.connect(self._on_toggle)
            row.addWidget(switch, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._rows.addLayout(row)

    def _on_toggle(self, on: bool) -> None:
        key = self.sender().property("dataset")
        if key:
            dataset_settings.set_on(str(key), on)
            try:
                from app.rotation import rotation
                rotation.refresh()          # a cleared pair may join the program
            except RuntimeError:
                pass
