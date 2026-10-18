"""
Diagnostics panel — the operator's side of `app/diagnostics/`.

Three questions, in order: **is this machine telling home about itself**,
**is anything wrong right now** (the check suite), and **what has it
reported**. Plus the one button an operator needs when a screen does
something odd: *Report a problem*, which sends their words with every log
tail, so "it glitched" arrives as a time, a build and the logs.

The status is a dot, never coloured type; the one primary is "Run all
checks". Only switching reporting on or off is admin-gated (hidden, not
disabled); reporting a problem never is.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from app import brand
from app.admin import admin
from app.config import config
from app.diagnostics import checks, settings as diag_settings
from app.diagnostics.service import diagnostics
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, SelectableChip, StatusDot, eyebrow,
)
from app.widgets.helpers import clear_layout, divider, label

_CHECK_COLOR = {checks.PASS: brand.STATUS_ONLINE, checks.WARN: brand.STATUS_PENDING,
                checks.FAIL: brand.STATUS_FAULT, checks.SKIP: brand.STATUS_IDLE}
_SEVERITY_COLOR = {"fatal": brand.STATUS_FAULT, "error": brand.STATUS_FAULT,
                   "warn": brand.STATUS_PENDING, "info": brand.STATUS_IDLE}


def _when(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso)
        return t.strftime("%b %d %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def _row(color: str, title: str, detail: str) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 6, 0, 6)
    row.setSpacing(10)
    row.addWidget(StatusDot(color), alignment=Qt.AlignmentFlag.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = label(title, "stat_value")
    t.setWordWrap(True)
    t.setStyleSheet("font-size: 14px;")
    col.addWidget(t)
    if detail:
        d = label(detail, "stat_label")
        d.setWordWrap(True)
        d.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        d.setMinimumWidth(0)
        col.addWidget(d)
    row.addLayout(col, stretch=1)
    return w


class DiagnosticsPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        diagnostics.changed.connect(self._refresh)
        diagnostics.suite_progress.connect(self._on_progress)
        diagnostics.suite_done.connect(self._on_suite_done)
        admin.lock_state_changed.connect(self._apply_lock)
        self._apply_lock(admin.unlocked)
        self._refresh()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        accent = config.active_team.primary_color
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Reporting ─────────────────────────────────────────────────────
        root.addWidget(eyebrow("Reporting to home"))
        root.addSpacing(8)
        card = RoundedFrame(fill=brand.CARBON_SURF2, border=brand.CARBON_LINE,
                            radius=brand.R_BTN)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self._dot = StatusDot()
        top.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._state_lbl = label("", "stat_value")
        self._state_lbl.setWordWrap(True)
        self._state_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._state_lbl.setMinimumWidth(0)
        top.addWidget(self._state_lbl, stretch=1)
        lay.addLayout(top)
        self._why_lbl = label("", "stat_label")
        self._why_lbl.setWordWrap(True)
        lay.addWidget(self._why_lbl)
        root.addWidget(card)
        root.addSpacing(10)

        # Admin: choose for this machine.
        self._admin_box = QWidget()
        chips = QHBoxLayout(self._admin_box)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        self._chips: dict[str, SelectableChip] = {}
        for key, text in (("auto", "Default"), ("on", "On"), ("off", "Off")):
            chip = SelectableChip(text)
            chip.setFixedWidth(100)
            chip.clicked.connect(lambda _=False, k=key: self._set_reporting(k))
            self._chips[key] = chip
            chips.addWidget(chip)
        chips.addStretch()
        root.addWidget(self._admin_box)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Checks ────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Check suite"))
        root.addSpacing(8)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._run_btn = RoundedButton("Run all checks", variant="primary", accent=accent)
        self._run_btn.clicked.connect(diagnostics.run_full)
        actions.addWidget(self._run_btn)
        self._send_btn = RoundedButton("Send status now", variant="secondary")
        self._send_btn.clicked.connect(diagnostics.send_now)
        actions.addWidget(self._send_btn)
        actions.addStretch()
        root.addLayout(actions)
        root.addSpacing(6)
        self._checks_lbl = label("", "stat_label")
        self._checks_lbl.setWordWrap(True)
        root.addWidget(self._checks_lbl)
        root.addSpacing(6)
        self._checks = QVBoxLayout()
        self._checks.setSpacing(0)
        root.addLayout(self._checks)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Report a problem ──────────────────────────────────────────────
        root.addWidget(eyebrow("Report a problem"))
        root.addSpacing(8)
        help_lbl = label(
            "Say what you saw and when: which screen, what it did, what you had "
            "just pressed. It goes home with this machine's logs and a fresh "
            "check of everything.", "stat_label")
        help_lbl.setWordWrap(True)
        root.addWidget(help_lbl)
        root.addSpacing(8)
        self._note = QPlainTextEdit()
        self._note.setPlaceholderText("e.g. Screen B went black for a few seconds "
                                      "right after the queue alert, about 2:15.")
        self._note.setFixedHeight(96)
        root.addWidget(self._note)
        root.addSpacing(8)
        row = QHBoxLayout()
        self._report_btn = RoundedButton("Send report", variant="secondary")
        self._report_btn.clicked.connect(self._on_report)
        row.addWidget(self._report_btn)
        self._report_lbl = label("", "stat_label")
        self._report_lbl.setWordWrap(True)
        row.addWidget(self._report_lbl, stretch=1)
        root.addLayout(row)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Recent ────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Recent reports"))
        root.addSpacing(8)
        self._recent = QVBoxLayout()
        self._recent.setSpacing(0)
        root.addLayout(self._recent)

    # ── Behaviour ─────────────────────────────────────────────────────────

    def _apply_lock(self, unlocked: bool) -> None:
        self._admin_box.setVisible(bool(unlocked))

    def _set_reporting(self, key: str) -> None:
        diagnostics.set_enabled({"auto": None, "on": True, "off": False}[key])

    def _on_report(self) -> None:
        eid = diagnostics.report_problem(self._note.toPlainText())
        if eid:
            self._note.clear()
            self._report_lbl.setText("Sent with this machine's logs." if diagnostics.reporting
                                     else "Kept on this machine (reporting is off).")
        else:
            self._report_lbl.setText("Couldn't save the report: is the data folder full?")

    def _on_progress(self, text: str) -> None:
        self._checks_lbl.setText(text)

    def _on_suite_done(self, _results) -> None:
        self._refresh()

    def _refresh(self) -> None:
        on, ready = diagnostics.reporting, diagnostics.sync_ready()
        self._dot.set_color(brand.STATUS_ONLINE if on and ready else
                            brand.STATUS_PENDING if on else brand.STATUS_IDLE)
        self._state_lbl.setText(diagnostics.describe())
        waiting = diagnostics.pending()
        self._why_lbl.setText(
            f"Reporting is {diag_settings.why()}. "
            + (f"{waiting} report{'s' if waiting != 1 else ''} waiting to go."
               if waiting else "Nothing waiting."))
        chosen = diag_settings.load()["enabled"]
        current = "auto" if chosen is None else ("on" if chosen else "off")
        for key, chip in self._chips.items():
            chip.set_active(key == current)

        busy = diagnostics.running_suite
        self._run_btn.setEnabled(not busy)
        self._run_btn.setText("Checking…" if busy else "Run all checks")
        if not busy:
            self._fill_checks()
        self._fill_recent()

    def _fill_checks(self) -> None:
        results = diagnostics.last_full or diagnostics.last_quick
        clear_layout(self._checks)
        if not results:
            self._checks_lbl.setText("The live checks run a minute after start and every few "
                                     "minutes after that. Run all checks to add the install's.")
            return
        which = "All checks" if diagnostics.last_full else "Live checks"
        s = checks.summarize(results)["counts"]
        self._checks_lbl.setText(f"{which}: {s['fail']} failing, {s['warn']} to look at, "
                                 f"{s['pass']} passing, {s['skip']} not applicable here.")
        for r in sorted(results, key=lambda r: (checks.RANK[r.status], r.name)):
            detail = r.detail.splitlines()[0] if r.status in (checks.PASS, checks.SKIP) \
                else "\n".join(r.detail.splitlines()[:6])
            self._checks.addWidget(_row(_CHECK_COLOR[r.status], r.name, detail))

    def _fill_recent(self) -> None:
        clear_layout(self._recent)
        events = diagnostics.recent(12)
        if not events:
            self._recent.addWidget(label("Nothing reported yet.", "stat_label"))
            return
        for ev in events:
            sent = "sent" if ev.get("sent") else "waiting"
            w = _row(_SEVERITY_COLOR.get(ev.get("severity"), brand.STATUS_IDLE),
                     ev.get("summary", ""),
                     f"{_when(ev.get('at', ''))} · {ev.get('kind', '')} · {sent}")
            self._recent.addWidget(w)
