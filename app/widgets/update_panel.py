"""
Software Updates panel — the operator's side of `app/update/`.

Four questions, in the order somebody standing at the pit laptop asks them:
**what am I running**, **is there something newer**, **which feed am I on**,
and **how do I get back** if the new one is wrong.

Three things about how it is drawn:

- **The state is a dot, never coloured type.** Red on carbon is 2.8:1 and the
  playbook forbids it outright, so the status colour lives in a filled 10px
  circle — the `bw-dot` mark — and the sentence beside it stays white. That
  also keeps the red budget honest: a failed update is one red dot, not a
  paragraph of red text next to a red button.
- **One primary button, and only when there is something to press.** Its label
  changes with the state — Check → Install → Restart — and on a machine that
  cannot update at all it drops to a neutral outline, because a disabled
  `primary` still paints a full red block and would be the panel's one red
  thing while meaning nothing.
- **The token field is admin-gated; rollback is not.** A credential is setup,
  done once, and hidden the way the LED tuning is. Rollback is the recovery
  path for a display that has come up wrong in front of visitors — that is the
  same call the LED kill switch makes, and it is never behind a password.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout, QLineEdit, QMessageBox, QProgressBar, QSizePolicy, QVBoxLayout,
    QWidget,
)

from app import brand, paths, version
from app.admin import admin
from app.config import config
from app.update import install, settings, update
from app.update.release import UpdateError, set_token, token
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, SelectableChip, eyebrow, mono_font,
)
from app.widgets.helpers import divider, label
from app.widgets.toggle_switch import ToggleSwitch

_CHANNEL_BLURB = {
    "stable": "Tagged releases only. What every pit machine should be on.",
    "beta":   "Also takes prereleases. Put one machine here to try a build "
              "before the rest of the pit gets it.",
}

# Which status colour each state of the service wears. `available` is amber for
# the same reason a pending status dot is: something is waiting on a person.
_STATE_COLOR = {
    "idle":       brand.STATUS_IDLE,
    "checking":   brand.STATUS_IDLE,
    "up_to_date": brand.STATUS_ONLINE,
    "available":  brand.STATUS_PENDING,
    "working":    brand.WHITE,
    "ready":      brand.STATUS_ONLINE,
    "error":      brand.STATUS_FAULT,
}


class _Dot(QWidget):
    """The brand's status dot — a filled circle, and the only thing on this
    panel allowed to be red."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(brand.STATUS_IDLE)
        self.setFixedSize(10, 10)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(10, 10)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(self.rect())
        p.end()


class UpdatePanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        update.state_changed.connect(self._on_state)
        update.progressed.connect(self._on_progress)
        admin.lock_state_changed.connect(self._apply_lock)
        config.team_changed.connect(self._on_team_changed)
        self._apply_lock(admin.unlocked)
        self._refresh()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        accent = config.active_team.primary_color
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── This build ────────────────────────────────────────────────────
        root.addWidget(eyebrow("This build"))
        root.addSpacing(8)
        self._build_lbl = label("", "stat_value")
        self._build_lbl.setFont(mono_font())
        self._build_lbl.setWordWrap(True)
        root.addWidget(self._build_lbl)
        root.addSpacing(4)
        self._install_lbl = label("", "stat_label")
        self._install_lbl.setWordWrap(True)
        root.addWidget(self._install_lbl)
        root.addSpacing(16)

        # ── Status ────────────────────────────────────────────────────────
        self._card = RoundedFrame(fill=brand.CARBON_SURF2,
                                  border=brand.CARBON_LINE, radius=brand.R_BTN)
        card = QVBoxLayout(self._card)
        card.setContentsMargins(14, 12, 14, 12)
        card.setSpacing(8)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._dot = _Dot()
        # Top-aligned: the sentence wraps to three lines on a narrow column and
        # a vertically centred dot would drift away from the line it labels.
        status_row.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._status_lbl = label("", "stat_value")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                                       QSizePolicy.Policy.Preferred)
        self._status_lbl.setMinimumWidth(0)
        status_row.addWidget(self._status_lbl, stretch=1)
        card.addLayout(status_row)

        self._notes_lbl = label("", "stat_label")
        self._notes_lbl.setWordWrap(True)
        self._notes_lbl.setVisible(False)
        card.addWidget(self._notes_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setVisible(False)
        card.addWidget(self._progress)
        root.addWidget(self._card)
        root.addSpacing(12)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._action_btn = RoundedButton("Check for updates", variant="primary",
                                         accent=accent)
        self._action_btn.clicked.connect(self._on_action)
        actions.addWidget(self._action_btn)
        self._recheck_btn = RoundedButton("Check again", variant="secondary")
        self._recheck_btn.clicked.connect(update.check)
        self._recheck_btn.setVisible(False)
        actions.addWidget(self._recheck_btn)
        actions.addStretch()
        root.addLayout(actions)
        root.addSpacing(6)
        help_lbl = label(
            "Downloading is never automatic and never interrupts the display — "
            "it runs in the background, the new version is checked on this "
            "machine before it is allowed to go live, and it starts the next "
            "time the app opens. Do not update during an event.", "stat_label")
        help_lbl.setWordWrap(True)
        root.addWidget(help_lbl)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Channel ───────────────────────────────────────────────────────
        root.addWidget(eyebrow("Release channel"))
        root.addSpacing(10)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._chips: dict[str, SelectableChip] = {}
        for name in settings.CHANNELS:
            chip = SelectableChip(name.capitalize())
            chip.setFixedWidth(110)
            chip.clicked.connect(lambda _=False, c=name: self._set_channel(c))
            self._chips[name] = chip
            chips.addWidget(chip)
        chips.addStretch()
        root.addLayout(chips)
        root.addSpacing(8)
        self._channel_lbl = label("", "stat_label")
        self._channel_lbl.setWordWrap(True)
        root.addWidget(self._channel_lbl)
        root.addSpacing(14)

        auto = QHBoxLayout()
        auto.setSpacing(12)
        auto_text = label("Look for updates automatically", "stat_value")
        auto_text.setStyleSheet("font-size: 15px;")
        auto.addWidget(auto_text, stretch=1)
        self._auto = ToggleSwitch()
        self._auto.toggled.connect(update.set_auto_check)
        auto.addWidget(self._auto, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(auto)
        root.addSpacing(4)
        self._last_lbl = label("", "stat_label")
        self._last_lbl.setWordWrap(True)
        root.addWidget(self._last_lbl)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Versions on disk ──────────────────────────────────────────────
        root.addWidget(eyebrow("Versions on this machine"))
        root.addSpacing(8)
        self._versions_lbl = label("", "stat_label")
        self._versions_lbl.setFont(mono_font())
        self._versions_lbl.setWordWrap(True)
        root.addWidget(self._versions_lbl)
        root.addSpacing(10)
        self._rollback_btn = RoundedButton("Roll back to the previous version",
                                           variant="secondary")
        self._rollback_btn.clicked.connect(self._on_rollback)
        root.addWidget(self._rollback_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addSpacing(6)
        rb_help = label(
            "The old version stays on disk until it is two updates behind, so "
            "a build that looks wrong on the pit screens can be undone without "
            "the internet — including when the app will not open, from a "
            "command line:", "stat_label")
        rb_help.setWordWrap(True)
        root.addWidget(rb_help)
        root.addSpacing(4)
        # Mono, because the display face ligatures a double hyphen into a dash
        # and an operator typing what they see would get an unknown flag.
        rb_cmd = label('"Breakaway Pit Display" --rollback', "stat_label")
        rb_cmd.setFont(mono_font())
        root.addWidget(rb_cmd)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Access (admin) ────────────────────────────────────────────────
        self._token_block = QWidget()
        tb = QVBoxLayout(self._token_block)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(0)
        tb.addWidget(eyebrow("Update access"))
        tb.addSpacing(8)
        token_help = label(
            "The repository is private, so this machine needs a GitHub "
            "fine-grained token with Contents: read on it — and nothing else. "
            "Paste it once; it is stored beside the database, not in the app "
            "folder, so upgrading never loses it.", "stat_label")
        token_help.setWordWrap(True)
        tb.addWidget(token_help)
        tb.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_edit.setPlaceholderText("github_pat_…")
        row.addWidget(self._token_edit, stretch=1)
        save_btn = RoundedButton("Save", variant="secondary")
        save_btn.setFixedWidth(90)
        save_btn.clicked.connect(self._save_token)
        row.addWidget(save_btn)
        clear_btn = RoundedButton("Clear", variant="ghost")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._clear_token)
        row.addWidget(clear_btn)
        tb.addLayout(row)
        tb.addSpacing(6)
        self._token_state = label("", "stat_label")
        self._token_state.setWordWrap(True)
        tb.addWidget(self._token_state)
        root.addWidget(self._token_block)

        self._locked_note = label(
            "Update access is admin-only — unlock with the Breakaway mark, "
            "top left.", "stat_label")
        self._locked_note.setWordWrap(True)
        root.addWidget(self._locked_note)

    # ── State ─────────────────────────────────────────────────────────────

    def _refresh(self):
        v = version
        self._build_lbl.setText(
            f"{v.VERSION or '—'}   channel {v.CHANNEL}"
            + (f"   {v.COMMIT[:7]}" if v.COMMIT else "")
            + (f"   built {v.BUILT}" if v.BUILT else ""))

        # Where this copy physically is. Not *why* it cannot update — the
        # status card below says that, and saying it twice reads as two faults.
        root = install.install_root()
        if root is not None:
            running = install.running_version() or "?"
            self._install_lbl.setText(f"Installed at {root} — running {running}")
        elif not paths.is_frozen():
            self._install_lbl.setText(f"Running from the checkout at "
                                      f"{paths.resource_root()}")
        else:
            self._install_lbl.setText(f"Unpacked at {install.executable().parent}")

        prefs = settings.load()
        channel = prefs["channel"]
        for name, chip in self._chips.items():
            chip.set_active(name == channel)
        self._channel_lbl.setText(_CHANNEL_BLURB.get(channel, ""))

        self._auto.blockSignals(True)
        self._auto.setChecked(bool(prefs["auto_check"]))
        self._auto.blockSignals(False)

        last = prefs["last_check"]
        self._last_lbl.setText(
            f"Last checked {last} UTC — {prefs['last_result']}." if last
            else f"Every {prefs['check_interval_hours']} hours. Not checked yet "
                 "this install.")

        versions = install.installed_versions()
        pointer = install.read_pointer()
        if versions:
            lines = []
            for name in sorted(versions, key=v.parse, reverse=True):
                marks = []
                if name == pointer.get("current"):
                    marks.append("current")
                if name == pointer.get("previous"):
                    marks.append("previous")
                if name == install.running_version():
                    marks.append("running")
                lines.append(f"{name}" + (f"   {', '.join(marks)}" if marks else ""))
            self._versions_lbl.setText("\n".join(lines))
        else:
            self._versions_lbl.setText("—")
        self._rollback_btn.setEnabled(bool(install.previous_version()))

        self._token_state.setText(
            "A token is stored on this machine." if token()
            else "No token stored — updates are off.")

        self._on_state(update.state)

    def _on_state(self, state: str):
        message = update.message or (
            update.blocked_reason if not update.supported
            else "Ready to check.")
        if not update.supported and state != "working":
            state = "error"
        self._status_lbl.setText(message)
        # White type, whatever the state. The colour is the dot's job — red on
        # carbon is 2.8:1 and never allowed to carry a letterform (§03).
        self._status_lbl.setStyleSheet(
            f"color: {brand.WHITE}; background: transparent;")
        self._dot.set_color(_STATE_COLOR.get(state, brand.STATUS_IDLE))

        release = update.release
        show_notes = state == "available" and release is not None and release.notes
        self._notes_lbl.setVisible(bool(show_notes))
        if show_notes:
            self._notes_lbl.setText(release.notes[:600])

        busy = state in ("checking", "working")
        self._progress.setVisible(state == "working")
        if state != "working":
            self._progress.setValue(0)

        labels = {
            "available": "Download and install",
            "ready":     "Restart now",
            "checking":  "Checking…",
            "working":   "Working…",
        }
        self._action_btn.setText(labels.get(state, "Check for updates"))
        actionable = update.supported or state == "ready"
        self._action_btn.setEnabled(not busy and actionable)
        # A disabled `primary` is still a filled red block, so a machine that
        # cannot update would spend its one red on a button that does nothing.
        self._action_btn.set_variant("primary" if actionable else "secondary")
        self._recheck_btn.setVisible(state in ("up_to_date", "error")
                                     and update.supported)
        self._recheck_btn.setEnabled(not busy)

    def _on_progress(self, message: str, fraction: float):
        self._status_lbl.setText(message)
        self._progress.setVisible(True)
        self._progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

    def _apply_lock(self, unlocked: bool):
        # Hidden, not disabled — a greyed-out credential field is an invitation
        # to go looking for the password.
        self._token_block.setVisible(unlocked)
        self._locked_note.setVisible(not unlocked)

    def _on_team_changed(self, team):
        self._action_btn.set_accent(team.primary_color)

    # ── Actions ───────────────────────────────────────────────────────────

    def _on_action(self):
        state = update.state
        if state == "available":
            update.install_available()
        elif state == "ready":
            self._restart()
        else:
            update.check()

    def _restart(self):
        answer = QMessageBox.question(
            self, "Restart into the new version",
            f"Close every screen and start {update.ready_version}?\n\n"
            "The audience screens go dark for a few seconds.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if update.restart():
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()
        else:
            QMessageBox.warning(self, "Could not restart",
                                "Close the app and open it from the shortcut; "
                                "the new version will start.")

    def _on_rollback(self):
        previous = install.previous_version()
        answer = QMessageBox.question(
            self, "Roll back",
            f"Go back to {previous}?\n\nIt starts the next time the app opens. "
            "Your database, checklists and CAN names are not touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            update.rollback()
        except UpdateError as exc:
            QMessageBox.warning(self, "Could not roll back", str(exc))
        self._refresh()

    def _set_channel(self, channel: str):
        update.set_channel(channel)
        self._refresh()

    def _save_token(self):
        value = self._token_edit.text().strip()
        if not value:
            return
        set_token(value)
        self._token_edit.clear()
        self._refresh()
        update.check()

    def _clear_token(self):
        set_token("")
        self._token_edit.clear()
        self._refresh()
