"""
Robot Logs panel — import Phoenix exports and name the CAN ids.

Two jobs:

1. **The CAN-id translation table.** A log only ever says "TalonFX 11". Nothing
   in the file can tell you that is the front-left drive motor, so this is a
   hand-maintained map, typed once per robot. Rows appear automatically the
   first time a device shows up in an import; the names you type persist across
   every future import and survive deleting the log they came from.

2. **Import.** Runs on a worker thread — a 3.85 GB file takes about a minute and
   would otherwise freeze the whole app.

Naming is not admin-gated: it is data entry, and getting the pit's motor names
right matters more than protecting them. Deleting a session is gated, because
it destroys data.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app import brand
from app.admin import admin
from app.config import config
from app.db import db
from app.robot import ImportError_, delete_session, import_log, repository as repo
from app.widgets.brand_widgets import RoundedButton, RoundedFrame, eyebrow, mono_font
from app.widgets.helpers import divider, label

# Device types the CTRE exports use. Free text would invite typos that silently
# create a second, unmatched device row.
DEVICE_TYPES = ["TalonFX", "CANcoder", "Pigeon2", "TalonFXS", "CANrange",
                "CANdi", "Pigeon", "PDH", "PDP", "Other"]

SUBSYSTEMS = ["", "Drivetrain", "Shooter", "Intake", "Indexer", "Climber",
              "Turret", "Vision", "Electrical"]

# Column indices for the CAN map table — named so adding a column does not
# mean hunting for bare integers.
_COL_TYPE, _COL_CANID, _COL_DETECTED, _COL_NAME, _COL_SUBSYSTEM, _COL_USED = range(6)


class _ImportWorker(QThread):
    """Runs one import off the GUI thread."""

    progressed = pyqtSignal(str, float)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path: Path, db_path: Path, parent=None):
        super().__init__(parent)
        self._path = path
        self._db_path = db_path

    def run(self):
        try:
            result = import_log(
                self._path, self._db_path,
                progress=lambda m, f: self.progressed.emit(m, f))
            self.finished_ok.emit(result)
        except ImportError_ as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                       # pragma: no cover
            self.failed.emit(f"Import failed: {exc}")


class RobotLogPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _ImportWorker | None = None
        self._loading = False
        self._build()
        admin.lock_state_changed.connect(self._apply_lock)
        config.team_changed.connect(self._on_team_changed)
        self._apply_lock(admin.unlocked)
        self.refresh()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Import ────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Import a log"))
        root.addSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._import_btn = RoundedButton("Choose log file…", variant="primary",
                                         accent=config.active_team.primary_color)
        self._import_btn.clicked.connect(self._choose_file)
        row.addWidget(self._import_btn)
        self._import_status = label("", "stat_label")
        self._import_status.setWordWrap(True)
        row.addWidget(self._import_status, stretch=1)
        root.addLayout(row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        root.addSpacing(8)
        root.addWidget(self._progress)

        root.addSpacing(6)
        import_help = label(
            "Phoenix 6 “detailed” text exports (.txt). A 3.8 GB file takes about "
            "a minute; the app stays usable while it runs.", "stat_label")
        import_help.setWordWrap(True)
        root.addWidget(import_help)
        root.addSpacing(16)

        self._totals = RoundedFrame(fill=brand.CARBON_SURF2, border=brand.CARBON_LINE,
                                    radius=brand.R_BTN)
        tl = QVBoxLayout(self._totals)
        tl.setContentsMargins(14, 11, 14, 11)
        self._totals_lbl = label("", "stat_value")
        self._totals_lbl.setWordWrap(True)
        tl.addWidget(self._totals_lbl)
        root.addWidget(self._totals)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── CAN map ───────────────────────────────────────────────────────
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(eyebrow("CAN ID → name"))
        head.addStretch()
        self._unnamed_lbl = label("", "stat_label")
        head.addWidget(self._unnamed_lbl)
        root.addLayout(head)
        root.addSpacing(6)
        can_help = label(
            "A log only says “TalonFX 11”. Type the English name once per robot "
            "and every screen, chart and fault report uses it from then on. "
            "Names survive re-imports and outlive the logs they came from. "
            "“Detected” is what the robot itself reported was plugged in — "
            "TalonFX controllers know which motor is attached.",
            "stat_label")
        can_help.setWordWrap(True)
        root.addWidget(can_help)
        root.addSpacing(10)

        # Columns: type · CAN id · what the log detected · your name · subsystem · usage
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Device", "CAN ID", "Detected", "English name", "Subsystem", ""])
        hh = self._table.horizontalHeader()
        for c in (0, 1, 2, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        # Without this the stretch column can grow past the viewport and push
        # Subsystem and the usage flag off the right edge.
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hh.setStretchLastSection(False)
        # Rows tall enough that an in-cell editor is not cramped.
        self._table.verticalHeader().setDefaultSectionSize(32)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(240)
        self._table.setAlternatingRowColors(False)
        self._table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self._table)
        root.addSpacing(10)

        # add-a-row
        add = QHBoxLayout()
        add.setSpacing(8)
        self._new_type = QComboBox()
        self._new_type.addItems(DEVICE_TYPES)
        self._new_type.setFixedWidth(120)
        add.addWidget(self._new_type)
        self._new_id = QSpinBox()
        self._new_id.setRange(0, 62)          # CAN device ids are 0–62
        self._new_id.setFixedWidth(70)
        add.addWidget(self._new_id)
        self._new_label = QLineEdit()
        self._new_label.setPlaceholderText("English name, e.g. Front-Left Drive")
        add.addWidget(self._new_label, stretch=1)
        self._new_sub = QComboBox()
        self._new_sub.addItems(SUBSYSTEMS)
        self._new_sub.setEditable(True)
        self._new_sub.setFixedWidth(140)
        add.addWidget(self._new_sub)
        add_btn = RoundedButton("Add", variant="secondary",
                                accent=config.active_team.primary_color)
        add_btn.setFixedWidth(74)
        add_btn.clicked.connect(self._add_device)
        add.addWidget(add_btn)
        self._add_btn = add_btn
        root.addLayout(add)
        root.addSpacing(6)
        add_help = label(
            "Add a device before its first log if you already know the CAN map.",
            "stat_label")
        add_help.setWordWrap(True)
        root.addWidget(add_help)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Sessions ──────────────────────────────────────────────────────
        root.addWidget(eyebrow("Imported sessions"))
        root.addSpacing(10)
        self._sessions = QTableWidget(0, 6)
        self._sessions.setHorizontalHeaderLabels(
            ["Session", "Started", "Length", "Raw rows", "Stored", "Saved"])
        sh = self._sessions.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 6):
            sh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._sessions.verticalHeader().setVisible(False)
        self._sessions.setMinimumHeight(120)
        root.addWidget(self._sessions)
        root.addSpacing(10)

        self._delete_btn = RoundedButton("Delete selected session", variant="ghost")
        self._delete_btn.clicked.connect(self._delete_selected)
        root.addWidget(self._delete_btn)
        self._delete_note = label(
            "Deleting a session is admin-only — it destroys imported samples.",
            "stat_label")
        self._delete_note.setWordWrap(True)
        root.addSpacing(4)
        root.addWidget(self._delete_note)

    # ── Refresh ───────────────────────────────────────────────────────────

    def refresh(self):
        self._loading = True
        try:
            devices = repo.devices()
            self._table.setRowCount(len(devices))
            for r, dv in enumerate(devices):
                t = QTableWidgetItem(dv.device_type)
                t.setFlags(t.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(r, _COL_TYPE, t)

                cid = QTableWidgetItem(str(dv.can_id))
                cid.setFlags(cid.flags() & ~Qt.ItemFlag.ItemIsEditable)
                cid.setFont(mono_font())
                cid.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(r, _COL_CANID, cid)

                det = QTableWidgetItem(dv.detected or "—")
                det.setFlags(det.flags() & ~Qt.ItemFlag.ItemIsEditable)
                det.setFont(mono_font())
                if not dv.detected:
                    det.setForeground(Qt.GlobalColor.gray)
                    det.setToolTip("This device type does not report a motor.")
                else:
                    det.setToolTip("Reported by the controller via ConnectedMotor.")
                self._table.setItem(r, _COL_DETECTED, det)

                name = QTableWidgetItem(dv.label)
                name.setData(Qt.ItemDataRole.UserRole, dv.id)
                if not dv.label:
                    name.setForeground(Qt.GlobalColor.gray)
                self._table.setItem(r, _COL_NAME, name)

                sub = QTableWidgetItem(dv.subsystem)
                sub.setData(Qt.ItemDataRole.UserRole, dv.id)
                self._table.setItem(r, _COL_SUBSYSTEM, sub)

                seen = db.fetchone(
                    "SELECT COUNT(*) n FROM series WHERE device_id=?", (dv.id,))["n"]
                used = QTableWidgetItem("in use" if seen else "unused")
                used.setFlags(used.flags() & ~Qt.ItemFlag.ItemIsEditable)
                used.setForeground(Qt.GlobalColor.gray)
                self._table.setItem(r, _COL_USED, used)

            unnamed = repo.unnamed_count()
            self._unnamed_lbl.setText(
                "all named" if unnamed == 0 else f"{unnamed} still unnamed")
            self._unnamed_lbl.setStyleSheet(
                f"color: {brand.STATUS_ONLINE if unnamed == 0 else brand.STATUS_PENDING};"
                " background: transparent;")

            rows = repo.sessions()
            self._sessions.setRowCount(len(rows))
            for r, s in enumerate(rows):
                saved = (1 - s["stored_rows"] / s["raw_rows"]) * 100 if s["raw_rows"] else 0
                cells = [
                    s["source_name"],
                    s["started_at"] or "—",
                    f"{(s['duration_s'] or 0)/60:.1f} min",
                    f"{s['raw_rows']:,}" if s["raw_rows"] else "—",
                    f"{s['stored_rows']:,}" if s["stored_rows"] else "—",
                    f"{saved:.1f}%",
                ]
                for cidx, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if cidx >= 3:
                        it.setFont(mono_font())
                        it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                            | Qt.AlignmentFlag.AlignVCenter)
                    if cidx == 0:
                        it.setData(Qt.ItemDataRole.UserRole, s["id"])
                    self._sessions.setItem(r, cidx, it)

            t = repo.session_totals()
            if t["sessions"] == 0:
                self._totals_lbl.setText("No logs imported yet.")
            else:
                self._totals_lbl.setText(
                    f"{t['sessions']} session(s) · {t['raw_rows']:,} raw rows stored "
                    f"as {t['stored_rows']:,} · telemetry file "
                    f"{t['samples_bytes']/1e6:.0f} MB")
        finally:
            self._loading = False

    # ── CAN map editing ───────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() not in (_COL_NAME, _COL_SUBSYSTEM):
            return
        device_id = item.data(Qt.ItemDataRole.UserRole)
        if device_id is None:
            return
        name = self._table.item(item.row(), _COL_NAME).text()
        sub = self._table.item(item.row(), _COL_SUBSYSTEM).text()
        repo.set_device_name(device_id, name, sub)
        self._loading = True
        try:
            self._table.item(item.row(), _COL_NAME).setForeground(
                Qt.GlobalColor.gray if not name else Qt.GlobalColor.white)
        finally:
            self._loading = False
        unnamed = repo.unnamed_count()
        self._unnamed_lbl.setText(
            "all named" if unnamed == 0 else f"{unnamed} still unnamed")

    def _add_device(self):
        new_id = repo.add_device(
            self._new_type.currentText(), self._new_id.value(),
            self._new_label.text(), self._new_sub.currentText())
        if new_id is None:
            QMessageBox.information(
                self, "Already listed",
                f"{self._new_type.currentText()} {self._new_id.value()} is already "
                f"in the table — edit that row instead.")
            return
        self._new_label.clear()
        self.refresh()

    # ── Import ────────────────────────────────────────────────────────────

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a Phoenix detailed export", "",
            "Log exports (*.txt);;All files (*)")
        if path:
            self.start_import(Path(path))

    def start_import(self, path: Path):
        if self._worker is not None and self._worker.isRunning():
            return
        self._import_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._import_status.setText(f"Reading {path.name}…")
        self._worker = _ImportWorker(path, db_path=Path(db.path))
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, message: str, frac: float):
        self._import_status.setText(message)
        self._progress.setValue(int(frac * 1000))

    def _on_done(self, result):
        self._import_btn.setEnabled(True)
        self._progress.setVisible(False)
        note = ""
        if result.new_devices:
            note = (f" · {len(result.new_devices)} new device(s) need names: "
                    + ", ".join(result.new_devices[:6])
                    + ("…" if len(result.new_devices) > 6 else ""))
        self._import_status.setText(
            f"Imported {result.raw_rows:,} rows as {result.stored_rows:,} "
            f"({result.compression:.0f}× smaller) in {result.elapsed_s:.0f}s{note}")
        self.refresh()

    def _on_failed(self, message: str):
        self._import_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._import_status.setText(message)
        self._import_status.setStyleSheet(
            f"color: {brand.RED}; background: transparent;")

    # ── Sessions ──────────────────────────────────────────────────────────

    def _delete_selected(self):
        rows = self._sessions.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Nothing selected",
                                    "Select a session row first.")
            return
        item = self._sessions.item(rows[0].row(), 0)
        sid = item.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(
                self, "Delete session",
                f"Delete “{item.text()}” and all of its samples?\n\n"
                f"The CAN-id names are kept.") != QMessageBox.StandardButton.Yes:
            return
        delete_session(sid, Path(db.path))
        self.refresh()

    # ── Admin gating ──────────────────────────────────────────────────────

    def _apply_lock(self, unlocked: bool):
        self._delete_btn.setVisible(unlocked)
        self._delete_note.setVisible(not unlocked)

    def _on_team_changed(self, team):
        for b in (self._import_btn, self._add_btn):
            b.set_accent(team.primary_color)
