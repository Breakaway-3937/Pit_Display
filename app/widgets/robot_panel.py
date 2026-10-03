"""
Robot Logs panel — import Phoenix exports and name the CAN ids.

Two jobs:

1. **The CAN-id translation table.** A log only ever says "TalonFX 11". Nothing
   in the file can tell you that is the front-left drive motor, so this is a
   hand-maintained map, typed once per robot. Rows appear automatically the
   first time a device shows up in an import; the names you type persist across
   every future import and survive deleting the log they came from.

2. **Import.** Runs on a worker thread — a 3.85 GB file takes about a minute and
   would otherwise freeze the whole app. **Import a folder** takes a whole
   drive at once (`app/robot/batch.py`): every log in every subfolder is copied
   onto this machine first and fingerprinted by content, logs imported before
   (same bytes, or the same recording at another length) are staged in a
   pop-up (skip, or re-import), the rest import newest first, one at a time,
   and each copy is deleted once its data is in the database.

Naming is not admin-gated: it is data entry, and getting the pit's motor names
right matters more than protecting them. Deleting a session is gated, because
it destroys data.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app import brand
from app.admin import admin
from app.config import config
from app.db import db
from app.robot import (
    ImportError_, batch, delete_session, import_log, owlet, repository as repo,
)
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


class _CopyWorker(QThread):
    """Batch step 1: find every log under the folder and copy it onto this
    machine (`batch.copy_in`). Nothing is imported here."""

    progressed = pyqtSignal(str, float)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, root: Path, db_path: Path, parent=None):
        super().__init__(parent)
        self._root = root
        self._db_path = db_path
        self.stop = False

    def run(self):
        try:
            self.progressed.emit(f"Looking through {self._root.name} and every folder in it…", 0.0)
            plan = batch.find_logs(self._root)
            batch.copy_in(plan, progress=lambda m, f: self.progressed.emit(m, f),
                          cancelled=lambda: self.stop)
            self.progressed.emit("Comparing with the logs already imported…", 1.0)
            batch.mark_duplicates(plan, self._db_path)
            self.finished_ok.emit(plan)
        except batch.CopyError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                       # pragma: no cover
            self.failed.emit(f"Copying failed: {exc}")


class _BatchImportWorker(QThread):
    """
    Batch step 3: import the queue one file at a time, from the local copies.
    A file that fails is reported and the rest carry on.

    **Pipelined across cores** (Brayden, 2026-10-03: use the whole machine).
    The database takes one writer at a time, and the import loop is Python, so
    imports themselves stay one after another; owlet is a separate process,
    so while one log imports, the next `LOOKAHEAD` hoots are already being
    extracted on their own threads (`ingest.prepare_hoot`). A conversion
    that fails is retried by the import itself, which then reports why.
    """

    LOOKAHEAD = 2

    progressed = pyqtSignal(str, float)
    file_done = pyqtSignal(str, str)          # file name, "" or the error
    all_done = pyqtSignal(int, list)          # imported, [(name, error)]

    def __init__(self, jobs: list, db_path: Path, parent=None):
        super().__init__(parent)
        self._jobs = jobs                     # [(batch.Found, session id to replace or None)]
        self._db_path = db_path
        self._keep = batch.keep_for_upload()
        self.stop = False

    def run(self):
        # Whatever happens in here, the panel hears the end: an exception that
        # skipped `all_done` left it showing "Importing…" forever.
        self._ok, failed = 0, []
        try:
            self._run(failed)
        except BaseException as exc:          # pragma: no cover
            failed.append(("(the batch)", f"stopped: {type(exc).__name__}: {exc}"))
        self.all_done.emit(self._ok, failed)

    def _run(self, failed: list):
        from concurrent.futures import ThreadPoolExecutor
        from app.robot.ingest import prepare_hoot
        n = len(self._jobs)
        pool = ThreadPoolExecutor(max_workers=max(1, self.LOOKAHEAD),
                                  thread_name_prefix="owlet-ahead")
        ahead: dict[int, object] = {}

        def look_ahead(start: int) -> None:
            for j in range(start, min(n, start + 1 + self.LOOKAHEAD)):
                f = self._jobs[j][0]
                if j not in ahead and f.dest is not None and f.dest.suffix.lower() == ".hoot":
                    ahead[j] = pool.submit(prepare_hoot, f.dest)

        try:
            self._import_all(n, failed, look_ahead, ahead)
        finally:
            # Stopped early: let running conversions finish, then clear them.
            for fut in ahead.values():
                if not fut.cancel():
                    try:
                        fut.result().discard()
                    except Exception:
                        pass
            pool.shutdown(wait=True)

    def _import_all(self, n, failed, look_ahead, ahead):
        for i, (f, replace) in enumerate(self._jobs, 1):
            if self.stop:
                break
            look_ahead(i - 1)                 # this hoot, and the next ones extract meanwhile
            head = f"Importing {i} of {n}: {f.name}"
            self.progressed.emit(head, (i - 1) / n)
            prepared = None
            fut = ahead.pop(i - 1, None)
            if fut is not None:
                if not fut.done():
                    self.progressed.emit(f"{head} · owlet is still extracting it…", (i - 1) / n)
                try:
                    prepared = fut.result()
                except Exception:
                    prepared = None           # the import converts again and says why
            try:
                if replace is not None:
                    delete_session(replace, self._db_path)
                import_log(f.dest, self._db_path, archive_path=f.dest, fp=f.fp,
                           prepared=prepared,
                           progress=lambda m, frac, head=head, i=i:
                               self.progressed.emit(f"{head} · {m}", (i - 1 + frac) / n))
                self._ok += 1
                # In the database now: the copy goes, unless team sync still
                # has to upload the original (the engine deletes it after).
                if not self._keep:
                    batch.discard(f.dest, self._db_path)
                self.file_done.emit(f.name, "")
            except ImportError_ as exc:
                failed.append((f.name, str(exc)))
                self.file_done.emit(f.name, str(exc))
            except Exception as exc:                   # pragma: no cover
                failed.append((f.name, f"Import failed: {exc}"))
                self.file_done.emit(f.name, str(exc))


class _DuplicatesDialog(QDialog):
    """
    Logs in the folder whose contents match ones imported before (`batch.py`:
    the same bytes, or the same recording at another length). They're already
    copied onto this machine; this decides whether to import them again.
    Re-importing replaces the earlier session, so it's offered only for a
    session imported on this machine and only with the admin lock open
    (deleting a session is admin-only); otherwise the box says why not.
    """

    def __init__(self, dups: list, unlocked: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Already imported")
        self.setMinimumWidth(720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(12)
        head = label(f"{len(dups)} of these logs match ones imported before", "stat_value")
        head.setWordWrap(True)
        lay.addWidget(head)
        note = label("Matched by their contents, not their names. “Same file” is "
                     "identical, byte for byte. “Same recording” starts the same but is "
                     "a different length (a longer copy is ticked for you). Tick any "
                     "to import again; that replaces the earlier session. Unticked "
                     "same files are skipped and their copies deleted; an unticked "
                     "different copy is kept on this machine.", "stat_label")
        note.setWordWrap(True)
        lay.addWidget(note)

        table = QTableWidget(len(dups), 4)
        table.setHorizontalHeaderLabels(["Re-import", "Log", "Match", "Imported before"])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(46)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._boxes: list[tuple[QCheckBox, object]] = []
        for r, f in enumerate(dups):
            prev = f.duplicate_of
            box = QCheckBox()
            local = batch.is_local(prev)
            box.setEnabled(local and unlocked)
            box.setChecked(box.isEnabled() and f.match == "same_recording" and f.longer)
            if not local:
                box.setToolTip("Imported on another pit and synced here; it can only "
                               "be replaced where it was imported.")
            elif not unlocked:
                box.setToolTip("Replacing a session is admin-only: unlock with the "
                               "Breakaway mark first.")
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(12, 0, 12, 0)
            cl.addWidget(box)
            table.setCellWidget(r, 0, cell)
            name = QTableWidgetItem(str(f.rel))
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, 1, name)
            if f.match == "exact":
                kind = "Same file"
            else:
                was = (prev.get("source_bytes") or 0) / 1e6
                kind = (f"Same recording, {'longer' if f.longer else 'shorter'} "
                        f"({f.size / 1e6:.1f} MB, was {was:.1f})")
            k = QTableWidgetItem(kind)
            k.setFlags(k.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, 2, k)
            where = (f"session {prev['id']}, imported {_local_time(prev.get('imported_at'))}"
                     if local else f"synced from {prev.get('origin_name') or 'another pit'}")
            when = QTableWidgetItem(where)
            when.setFlags(when.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, 3, when)
            self._boxes.append((box, f))
        table.setMinimumHeight(min(420, 60 + 46 * len(dups)))
        lay.addWidget(table)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        skip = RoundedButton("Skip all of these", variant="secondary")
        skip.setMinimumHeight(46)
        skip.clicked.connect(self._skip_all)
        buttons.addWidget(skip)
        go = RoundedButton("Continue", variant="primary")
        go.setMinimumHeight(46)
        go.clicked.connect(self.accept)
        buttons.addWidget(go)
        lay.addLayout(buttons)

    def _skip_all(self):
        for box, _f in self._boxes:
            box.setChecked(False)
        self.accept()

    def chosen(self) -> list:
        """The duplicates to import again, as (Found, session id to replace)."""
        return [(f, f.duplicate_of["id"]) for box, f in self._boxes if box.isChecked()]


def _hold_analysis(held: bool) -> None:
    """Analysis waits while a batch imports, then takes every new log in turn
    (`app/ai/service.py`). Fine when analysis isn't running on this machine."""
    try:
        from app.ai.service import analysis
        analysis.hold(held)
    except Exception:
        pass


def _local_time(utc: str | None) -> str:
    """SQLite's datetime('now') (UTC) as this machine's local time."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(utc)).replace(tzinfo=timezone.utc).astimezone()
        return t.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(utc or "")[:16]


def _origin(s) -> tuple[str, str]:
    """(the From cell, its tooltip): which machine holds the original log."""
    if not str(s["source_file"] or "").startswith("sync:"):
        return "This machine", f"Imported here from {s['source_file']}"
    if s["origin"]:
        name = s["origin_name"] or s["origin"]
        return name, (f"Imported on {name} ({s['origin']}); the original file "
                      "is on that machine. This copy came through team sync.")
    return "Team sync", ("Came through team sync; which machine imported it "
                         "shows after the next sync cycle reaches the hub.")


class RobotLogPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _ImportWorker | None = None
        self._copier: _CopyWorker | None = None
        self._batch: _BatchImportWorker | None = None
        self._loading = False
        self._build()
        admin.lock_state_changed.connect(self._apply_lock)
        config.team_changed.connect(self._on_team_changed)
        # Sessions and CAN names also arrive by sync; without this the list
        # stayed empty until a restart while the boards already showed the log.
        config.logs_changed.connect(self.refresh)
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
        # The folder is the primary: the whole drive in one go is what a
        # five-minute window with the robot needs.
        self._folder_btn = RoundedButton("Import a folder…", variant="primary",
                                         accent=config.active_team.primary_color)
        self._folder_btn.clicked.connect(self._choose_folder)
        row.addWidget(self._folder_btn)
        self._import_btn = RoundedButton("One file…", variant="secondary",
                                         accent=config.active_team.primary_color)
        self._import_btn.clicked.connect(self._choose_file)
        row.addWidget(self._import_btn)
        self._stop_btn = RoundedButton("Stop", variant="ghost")
        self._stop_btn.clicked.connect(self._stop_batch)
        self._stop_btn.setVisible(False)
        row.addWidget(self._stop_btn)
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
            "Import a folder takes a whole drive: every .hoot and .wpilog in it and "
            "in every folder inside it is copied onto this machine first (the drive "
            "can come out once copying is done). Logs are matched by their contents, "
            "never their names: ones imported before are listed for you to skip or "
            "import again, the rest import newest first, and each copy is deleted "
            "once it's in the database. "
            "A .hoot is extracted with owlet, so it takes longer than its size "
            "suggests; the app stays usable throughout.", "stat_label")
        import_help.setWordWrap(True)
        root.addWidget(import_help)

        # Which owlet this machine picked. Shown because "no owlet for your
        # platform" is the one import failure an operator can neither diagnose
        # nor fix from the error alone — and the pit machine is Windows while
        # every machine this is built on is not.
        root.addSpacing(4)
        owlet_line = label(owlet.describe(), "stat_label")
        owlet_line.setWordWrap(True)
        owlet_line.setFont(mono_font())
        if not owlet.available():
            owlet_line.setStyleSheet(
                f"color: {brand.STATUS_PENDING}; background: transparent;")
        root.addWidget(owlet_line)
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
        self._sessions = QTableWidget(0, 7)
        self._sessions.setHorizontalHeaderLabels(
            ["Session", "From", "Started", "Length", "Raw rows", "Stored", "Saved"])
        sh = self._sessions.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 7):
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
                origin, where = _origin(s)
                cells = [
                    s["source_name"],
                    origin,
                    s["started_at"] or "—",
                    f"{(s['duration_s'] or 0)/60:.1f} min",
                    f"{s['raw_rows']:,}" if s["raw_rows"] else "—",
                    f"{s['stored_rows']:,}" if s["stored_rows"] else "—",
                    f"{saved:.1f}%",
                ]
                for cidx, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if cidx == 1:
                        it.setToolTip(where)
                    if cidx >= 4:
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
            self, "Choose a robot log", "",
            "Robot logs (*.hoot *.wpilog);;"
            "Phoenix hoot (*.hoot);;WPILib DataLog (*.wpilog)")
        if path:
            self.start_import(Path(path))

    def _busy(self) -> bool:
        return any(w is not None and w.isRunning()
                   for w in (self._worker, self._copier, self._batch))

    def _set_busy(self, busy: bool, batch_run: bool = False) -> None:
        self._import_btn.setEnabled(not busy)
        self._folder_btn.setEnabled(not busy)
        self._stop_btn.setVisible(busy and batch_run)
        self._progress.setVisible(busy)
        if busy:
            self._progress.setValue(0)
            self._import_status.setStyleSheet("")

    # ── Batch: a whole folder ─────────────────────────────────────────────

    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose the folder with the robot logs")
        if path:
            self.start_batch(Path(path))

    def start_batch(self, root: Path):
        if self._busy():
            return
        self._set_busy(True, batch_run=True)
        _hold_analysis(True)
        self._import_status.setText(f"Looking through {root.name}…")
        self._copier = _CopyWorker(root, Path(db.path))
        self._copier.progressed.connect(self._on_progress)
        self._copier.finished_ok.connect(self._on_copied)
        self._copier.failed.connect(self._on_failed)
        self._copier.start()

    def _stop_batch(self):
        for w in (self._copier, self._batch):
            if w is not None and w.isRunning():
                w.stop = True
        self._import_status.setText("Stopping after this file…")

    def _on_copied(self, plan):
        stopped = self._copier is not None and self._copier.stop
        self._batch = None
        if not plan.found:
            self._set_busy(False)
            _hold_analysis(False)
            self._import_status.setText(
                f"No .hoot or .wpilog files in {plan.root.name} or the folders inside it."
                + (f" ({len(plan.empty)} empty file(s) skipped.)" if plan.empty else ""))
            return
        valid = len({f.fp.sha256 for f in plan.found if f.fp is not None and f.fp.valid})
        safe = f"{valid} log(s) are on this machine. The drive can come out."
        if stopped:
            self._set_busy(False)
            _hold_analysis(False)
            self._import_status.setText(
                "Stopped while copying; nothing was imported. The logs copied so far "
                "are on this machine; run the folder again to finish.")
            return
        jobs = [(f, None) for f in plan.queue]
        skipped = kept = 0
        if plan.duplicates:
            self._import_status.setText(safe + " Some were imported before.")
            dlg = _DuplicatesDialog(plan.duplicates, admin.unlocked, self)
            dlg.exec()
            again = dlg.chosen()
            jobs += sorted(again, key=lambda j: j[0].started or "", reverse=True)
            chosen = {id(f) for f, _sid in again}
            for f in plan.duplicates:
                if id(f) in chosen:
                    continue
                if f.match == "exact":
                    batch.discard(f.dest)       # its data is in the database already
                    skipped += 1
                else:
                    kept += 1                   # a different copy: kept, not imported
        self._batch_skipped, self._batch_kept = skipped, kept
        self._batch_invalid = [f.name for f in plan.invalid]
        self._batch_empty = len(plan.empty)
        self._batch_safe = safe
        if not jobs:
            self._set_busy(False)
            self._batch_done_text(0, [], safe + " Nothing new to import.")
            return
        self._batch = _BatchImportWorker(jobs, Path(db.path))
        self._batch.progressed.connect(self._on_progress)
        self._batch.file_done.connect(self._on_batch_file)
        self._batch.all_done.connect(self._on_batch_done)
        self._batch.start()

    def _on_batch_file(self, _name: str, error: str):
        if not error:
            self.refresh()
            # The boards follow along, but at most every 20 s: reloading every
            # screen after each of 60 logs kept the GUI thread busy enough to
            # look hung. The batch's end always notifies.
            import time
            now = time.monotonic()
            if now - getattr(self, "_last_notify", 0.0) >= 20.0:
                self._last_notify = now
                config.notify_logs_changed()

    def _on_batch_done(self, ok: int, failed: list):
        self._batch_done_text(ok, failed, f"Imported {ok} log(s).")

    def _batch_done_text(self, ok: int, failed: list, lead: str):
        stopped = self._batch is not None and self._batch.stop
        self._set_busy(False)
        if ok:
            config.notify_logs_changed()    # every board catches up once
        _hold_analysis(False)               # every log just imported queues now
        parts = [lead]
        if self._batch_skipped:
            parts.append(f"{self._batch_skipped} already imported (same contents), skipped.")
        if self._batch_kept:
            parts.append(f"{self._batch_kept} different copy/copies of earlier logs kept "
                         "on this machine, not imported.")
        if self._batch_invalid:
            parts.append(f"{len(self._batch_invalid)} not real logs, skipped: "
                         + ", ".join(self._batch_invalid[:3]))
        if self._batch_empty:
            parts.append(f"{self._batch_empty} empty file(s) skipped.")
        if stopped:
            parts.append("Stopped early; run the folder again to finish (imported "
                         "ones are recognised).")
        if failed:
            parts.append(f"{len(failed)} couldn't be read: "
                         + "; ".join(f"{n} ({e[:80]})" for n, e in failed[:3])
                         + ("…" if len(failed) > 3 else ""))
            self._import_status.setStyleSheet(
                f"color: {brand.STATUS_PENDING}; background: transparent;")
        self._import_status.setText(" ".join(parts))
        self.refresh()

    # ── One file ──────────────────────────────────────────────────────────

    def start_import(self, path: Path):
        if self._busy():
            return
        self._set_busy(True)
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
        self._set_busy(False)
        note = ""
        if result.new_devices:
            note = (f" · {len(result.new_devices)} new device(s) need names: "
                    + ", ".join(result.new_devices[:6])
                    + ("…" if len(result.new_devices) > 6 else ""))
        # Say what was dropped. A log that is a third unstorable is one somebody
        # needs to look at, and silence would read as a clean import.
        if result.skipped:
            note += f" · {result.skipped:,} record(s) skipped (unstorable type)"
        if result.enum_overflow:
            note += (" · too many distinct values, not stored: "
                     + ", ".join(result.enum_overflow[:3]))
        self._import_status.setText(
            f"Imported {result.raw_rows:,} rows as {result.stored_rows:,} "
            f"({result.compression:.0f}× smaller) in {result.elapsed_s:.0f}s{note}")
        self.refresh()
        # Every screen that reads the log picks the new one up now, rather than
        # after a power-cycle from the sidebar.
        config.notify_logs_changed()

    def _on_failed(self, message: str):
        self._set_busy(False)
        _hold_analysis(False)
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
        config.notify_logs_changed()

    # ── Admin gating ──────────────────────────────────────────────────────

    def _apply_lock(self, unlocked: bool):
        self._delete_btn.setVisible(unlocked)
        self._delete_note.setVisible(not unlocked)

    def _on_team_changed(self, team):
        for b in (self._folder_btn, self._import_btn, self._add_btn):
            b.set_accent(team.primary_color)
