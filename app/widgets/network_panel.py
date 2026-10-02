"""
Control → Pit Systems → Telemetry. Every link the pit depends on, and how it is doing.

This was the Pit Network panel, and the overhead displays are still here —
but a display going dark is one of four ways the pit loses a link, and the
operator standing at the control machine should not have to know which panel
each one lives on. So every link reports here, in one place, in the order
somebody looks when something on a screen is wrong:

| section | link | where the numbers come from |
|---|---|---|
| Event relay | this machine ⇄ `nexus.bh-stack.com`, and Nexus ⇄ the relay | `nexus.relay.telemetry()` — the socket's own counters, plus the relay's (webhooks, pulls, subscribers) carried on every message |
| Event feed | which door each piece of event data came through | `nexus.sources()` / `nexus.status_source` |
| Overhead displays | the Pis on the pit LAN | `webcast.telemetry()` |
| LED controller | the USB serial link | `leds` |
| Team sync | this machine ⇄ `sync.bh-stack.com` ⇄ the other pits and home | `sync.last` (the engine's `Report`), `sync.history` |
| Robot telemetry | logs off the robot, and the CAN-id names | `RobotLogPanel`, embedded whole |

Robot Logs used to be its own sidebar entry. It is embedded here unchanged —
import, the CAN-id table, delete — so everything called telemetry in this app
has one home.

**The relay's half is the part nothing else can show.** Whether Nexus is
reaching the relay at all — webhooks arriving, refused for a wrong token, or
never registered — is only visible from the relay's side, so it sends its
counters down the socket and this panel is where they land. A webhook count
that never moves while the pulls keep succeeding means the data is fine and
the registration at frc.nexus/api is not.

**It redraws on a signal, not on a timer.** Each link emits when it moves;
the one slow tick below only keeps the "12s ago" ages honest.

**Recent history is kept on purpose.** A display or a relay link that drops
and reconnects a second later is invisible in a live-only list: by the time
anybody looks, it is back. The Recent rows are how a flapping link shows up.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from app import brand, credentials
from app.admin import admin
from app.config import SCREEN_LABELS, config
from app.db.sync import settings as sync_settings
from app.db.sync import transfer
from app.db.sync.service import sync
from app.leds import leds
from app.nexus import nexus
from app.nexus import settings as nexus_settings
from app.webcast import lan_address, webcast
from app.webcast import settings as webcast_settings
from app.widgets.brand_widgets import RoundedButton, StatusDot, eyebrow
from app.widgets.helpers import divider, label
from app.widgets.robot_panel import RobotLogPanel
from app.widgets.toggle_switch import ToggleSwitch

# (pref key, what the switch says). `enabled` first: it's the one people want.
_SYNC_SWITCHES = (
    ("enabled", "Sync on this machine"),
    ("pull_logs", "Download other pits' robot logs"),
    ("sync_files", "Team files: judges slides, the CAD model (~300 MB)"),
    ("sync_music", "Team music: every scanned song, both ways"),
    ("upload_raw", "Also upload original log files (home Wi-Fi only)"),
)

_RELAY_COLOR = {
    "live":         brand.STATUS_ONLINE,
    "connecting":   brand.STATUS_PENDING,
    "reconnecting": brand.STATUS_PENDING,
    "off":          brand.STATUS_IDLE,
}

_MODE_NAMES = {0: "solid", 1: "breathe", 2: "wipe", 3: "chase",
               4: "sparkle", 5: "rainbow", 6: "alert", 7: "off"}

_JOBS = (("status", "live snapshot"), ("pits", "pits"), ("map", "map"),
         ("inspection", "inspection"), ("teams", "teams"),
         ("alliances", "alliances"))


def _ago(seconds: float | None) -> str:
    """A duration an operator can read at a glance, never a raw float."""
    if seconds is None:
        return "never"
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _ago_ms(stamp_ms: int | float | None) -> str:
    """Age of a Unix-millisecond stamp from the relay, or `never`."""
    if not stamp_ms:
        return "never"
    return _ago(time.time() - float(stamp_ms) / 1000) + " ago"


def _bytes(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KB"
    return f"{count / (1024 * 1024):.1f} MB"


def _verdict(machine_id: str) -> str:
    """Home's last check of this machine's manifest (R8), as one line."""
    import json
    from app.db import db
    try:
        row = db.fetchone("SELECT data FROM sync_verdict WHERE uid = ?", (machine_id,))
        v = json.loads(row["data"]) if row else None
    except Exception:
        return ""
    return verdict_line(v)


def verdict_line(v: dict | None) -> str:
    if not v:
        return "Home hasn't checked this machine yet (it does after each night's sync)."
    when = str(v.get("checked_at") or "")[:16].replace("T", " ")
    if v.get("in_sync"):
        return f"In sync with home, checked {when}."
    parts = []
    for key, word in (("missing", "missing"), ("different", "different"),
                      ("extra", "not at home")):
        items = v.get(key) or []
        if items:
            names = ", ".join(i.partition("/")[2] or i for i in items[:3])
            parts.append(f"{len(items)} {word} ({names}{'…' if len(items) > 3 else ''})")
    if v.get("playlists_differ"):
        parts.append(f"{len(v['playlists_differ'])} playlist(s) differ")
    return f"Out of sync with home, checked {when}: " + "; ".join(parts) + "."


class NetworkPanel(QWidget):
    """Live view of every link the pit depends on."""

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Event relay ───────────────────────────────────────────────────
        root.addWidget(eyebrow("Event relay"))
        root.addSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        self._relay_dot = StatusDot()
        head.addWidget(self._relay_dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._relay_head = label("", "stat_value")
        self._relay_head.setWordWrap(True)
        self._relay_head.setStyleSheet("font-size: 15px;")
        head.addWidget(self._relay_head, stretch=1)
        root.addLayout(head)
        root.addSpacing(6)
        self._relay_link = label("", "stat_label")
        self._relay_link.setWordWrap(True)
        self._relay_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._relay_link)
        root.addSpacing(10)
        self._relay_side = label("", "stat_label")
        self._relay_side.setWordWrap(True)
        root.addWidget(self._relay_side)
        root.addSpacing(10)
        self._check_btn = RoundedButton("Ask the relay now", variant="secondary")
        self._check_btn.clicked.connect(self._check_relay)
        root.addWidget(self._check_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Event feed ────────────────────────────────────────────────────
        root.addWidget(eyebrow("Event feed"))
        root.addSpacing(8)
        self._feed = label("", "stat_label")
        self._feed.setWordWrap(True)
        root.addWidget(self._feed)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Overhead displays ─────────────────────────────────────────────
        root.addWidget(eyebrow("Overhead displays · pit network"))
        root.addSpacing(8)
        self._server_line = label("", "stat_label")
        self._server_line.setWordWrap(True)
        root.addWidget(self._server_line)
        root.addSpacing(10)
        self._live = label("", "stat_label")
        self._live.setWordWrap(True)
        self._live.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._live)
        root.addSpacing(10)
        self._refresh_btn = RoundedButton("Resend to all displays",
                                          variant="secondary")
        self._refresh_btn.clicked.connect(self._resend)
        root.addWidget(self._refresh_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── LED controller ────────────────────────────────────────────────
        root.addWidget(eyebrow("LED controller"))
        root.addSpacing(8)
        led_head = QHBoxLayout()
        led_head.setSpacing(10)
        self._led_dot = StatusDot()
        led_head.addWidget(self._led_dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._led_head = label("", "stat_value")
        self._led_head.setWordWrap(True)
        self._led_head.setStyleSheet("font-size: 15px;")
        led_head.addWidget(self._led_head, stretch=1)
        root.addLayout(led_head)
        root.addSpacing(6)
        self._leds = label("", "stat_label")
        self._leds.setWordWrap(True)
        root.addWidget(self._leds)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Team sync ─────────────────────────────────────────────────────
        root.addWidget(eyebrow("Team sync"))
        root.addSpacing(8)
        sync_head = QHBoxLayout()
        sync_head.setSpacing(10)
        self._sync_dot = StatusDot()
        sync_head.addWidget(self._sync_dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._sync_head = label("", "stat_value")
        self._sync_head.setWordWrap(True)
        self._sync_head.setStyleSheet("font-size: 15px;")
        sync_head.addWidget(self._sync_head, stretch=1)
        root.addLayout(sync_head)
        root.addSpacing(6)
        self._sync = label("", "stat_label")
        self._sync.setWordWrap(True)
        self._sync.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._sync)
        root.addSpacing(10)
        self._sync_btn = RoundedButton("Sync now", variant="secondary")
        self._sync_btn.clicked.connect(self._sync_now)
        root.addWidget(self._sync_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addSpacing(16)
        self._build_sync_settings(root)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Recent ────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Recent drops"))
        root.addSpacing(8)
        self._recent = label("", "stat_label")
        self._recent.setWordWrap(True)
        root.addWidget(self._recent)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Robot telemetry ───────────────────────────────────────────────
        root.addWidget(eyebrow("Robot telemetry · logs"))
        root.addSpacing(8)
        self.robot = RobotLogPanel()
        root.addWidget(self.robot)
        root.addStretch()

        webcast.telemetry_changed.connect(self._refresh)
        webcast.state_changed.connect(self._on_state_changed)
        webcast.viewers_changed.connect(self._on_viewers_changed)
        nexus.relay_changed.connect(self._refresh)
        nexus.state_changed.connect(self._on_feed_state)
        leds.connection_changed.connect(self._on_leds)
        leds.telemetry_changed.connect(self._refresh)
        leds.state_changed.connect(self._refresh)
        sync.state_changed.connect(self._refresh)
        sync.progress.connect(self._on_sync_progress)
        admin.lock_state_changed.connect(self._apply_lock)
        self._apply_lock(admin.unlocked)

        # One slow tick, only so the ages stay honest while nothing else is
        # happening. Everything that *matters* arrives on a signal; this is
        # cosmetic and is why it is five seconds and not one.
        self._age_tick = QTimer(self)
        self._age_tick.setInterval(5000)
        self._age_tick.timeout.connect(self._refresh)
        self._age_tick.start()

        self._refresh()

    # ── Bound methods, never lambdas: the services outlive this panel, and a
    # lambda left on their signals keeps firing.

    def _on_state_changed(self, _on: bool, _reason: str) -> None:
        self._refresh()

    def _on_viewers_changed(self, _count: int) -> None:
        self._refresh()

    def _on_feed_state(self, _state: str) -> None:
        self._refresh()

    def _on_leds(self, _connected: bool, _text: str) -> None:
        self._refresh()

    # ── Team sync settings (admin) ────────────────────────────────────────

    def _build_sync_settings(self, root: QVBoxLayout) -> None:
        """Name, switches, token and id. Hidden, not disabled, while locked."""
        self._sync_block = QWidget()
        sb = QVBoxLayout(self._sync_block)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(0)
        sb.addWidget(eyebrow("This machine"))
        sb.addSpacing(8)
        files_now = RoundedButton("Sync files now", variant="secondary")
        files_now.clicked.connect(self._sync_files_now)
        sb.addWidget(files_now, alignment=Qt.AlignmentFlag.AlignLeft)
        sb.addSpacing(4)
        files_help = label(
            "Moves CAD, slides and music this once, outside the night window: "
            "for good venue or hotel Wi-Fi.", "stat_label")
        files_help.setWordWrap(True)
        sb.addWidget(files_help)
        sb.addSpacing(12)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("What the other pits and home call it")
        self._name_edit.setMaxLength(80)
        self._name_edit.returnPressed.connect(self._save_name)
        name_row.addWidget(self._name_edit, stretch=1)
        name_btn = RoundedButton("Rename", variant="secondary")
        name_btn.setFixedWidth(110)
        name_btn.clicked.connect(self._save_name)
        name_row.addWidget(name_btn)
        sb.addLayout(name_row)
        sb.addSpacing(12)

        self._switches: dict[str, ToggleSwitch] = {}
        for key, text in _SYNC_SWITCHES:
            row = QHBoxLayout()
            row.setSpacing(12)
            words = label(text, "stat_value")
            words.setWordWrap(True)
            words.setStyleSheet("font-size: 15px;")
            row.addWidget(words, stretch=1)
            switch = ToggleSwitch()
            switch.setProperty("pref", key)
            switch.toggled.connect(self._on_switch)
            row.addWidget(switch, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._switches[key] = switch
            sb.addLayout(row)
            sb.addSpacing(10)
        sb.addSpacing(6)

        help_ = label(
            "The sync token is the hub's pit token. It goes to the secret "
            "folder beside the database, never the database.", "stat_label")
        help_.setWordWrap(True)
        sb.addWidget(help_)
        sb.addSpacing(8)
        tok_row = QHBoxLayout()
        tok_row.setSpacing(8)
        self._sync_token_edit = QLineEdit()
        self._sync_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._sync_token_edit.setPlaceholderText("Sync token")
        tok_row.addWidget(self._sync_token_edit, stretch=1)
        tok_save = RoundedButton("Save", variant="secondary")
        tok_save.setFixedWidth(90)
        tok_save.clicked.connect(self._save_sync_token)
        tok_row.addWidget(tok_save)
        tok_clear = RoundedButton("Clear", variant="ghost")
        tok_clear.setFixedWidth(80)
        tok_clear.clicked.connect(self._clear_sync_token)
        tok_row.addWidget(tok_clear)
        sb.addLayout(tok_row)
        sb.addSpacing(4)
        self._sync_token_state = label("", "stat_label")
        self._sync_token_state.setWordWrap(True)
        sb.addWidget(self._sync_token_state)
        sb.addSpacing(14)

        id_help = label(
            "Machine id: how the hub tells this machine's edits from every "
            "other's. Change it only to give a machine a name you chose, or "
            "to take back a reinstalled machine's old id. No two machines may "
            "ever share one.", "stat_label")
        id_help.setWordWrap(True)
        sb.addWidget(id_help)
        sb.addSpacing(8)
        id_row = QHBoxLayout()
        id_row.setSpacing(8)
        self._id_edit = QLineEdit()
        self._id_edit.setMaxLength(80)
        id_row.addWidget(self._id_edit, stretch=1)
        id_btn = RoundedButton("Change id…", variant="secondary")
        id_btn.setFixedWidth(130)
        id_btn.clicked.connect(self._change_machine_id)
        id_row.addWidget(id_btn)
        sb.addLayout(id_row)
        sb.addSpacing(4)
        self._id_state = label("", "stat_label")
        self._id_state.setWordWrap(True)
        sb.addWidget(self._id_state)
        root.addWidget(self._sync_block)

        self._sync_locked = label(
            "Sync settings are admin-only — unlock with the Breakaway mark, "
            "top left.", "stat_label")
        self._sync_locked.setWordWrap(True)
        root.addWidget(self._sync_locked)
        root.addSpacing(16)
        self._fill_sync_form()

    def _sync_files_now(self) -> None:
        sync.sync_files_now()

    def _fill_sync_form(self) -> None:
        """Put the stored values in the text fields (only on build and after a
        save, so a refresh never overwrites what someone is typing)."""
        prefs = sync.prefs
        self._name_edit.setText(prefs["machine_name"])
        self._id_edit.setText(prefs["machine_id"])
        self._draw_sync_form()

    def _draw_sync_form(self) -> None:
        prefs = sync.prefs
        for key, switch in self._switches.items():
            if switch.isChecked() != bool(prefs[key]):
                switch.blockSignals(True)
                switch.setChecked(bool(prefs[key]))
                switch.blockSignals(False)
                switch.update()
        where = credentials.source(sync_settings.TOKEN_NAME)
        self._sync_token_state.setText({
            "file": "Token saved on this machine.",
            "env": "Token set by the environment (PIT_SECRET_SYNC_TOKEN); "
                   "it overrides anything saved here.",
        }.get(where, "No token: sync is off until one is saved."))

    def _apply_lock(self, unlocked: bool) -> None:
        self._sync_block.setVisible(unlocked)
        self._sync_locked.setVisible(not unlocked)
        if not unlocked:
            self._sync_token_edit.clear()
            self._fill_sync_form()

    def _on_switch(self, on: bool) -> None:
        key = self.sender().property("pref")
        if key == "enabled":
            sync.set_enabled(on)
        else:
            sync.set_prefs(**{key: on})

    def _save_name(self) -> None:
        name = self._name_edit.text().strip()
        if name:
            sync.set_prefs(machine_name=name)
        self._fill_sync_form()

    def _save_sync_token(self) -> None:
        value = self._sync_token_edit.text().strip()
        if not value:
            return
        sync.set_token(value)
        self._sync_token_edit.clear()
        self._draw_sync_form()

    def _clear_sync_token(self) -> None:
        sync.set_token("")
        self._sync_token_edit.clear()
        self._draw_sync_form()

    def _change_machine_id(self) -> None:
        new = self._id_edit.text().strip()
        current = sync.prefs["machine_id"]
        if new == current:
            self._id_state.setText("That's already this machine's id.")
            return
        if not sync_settings.MACHINE_ID_RE.match(new):
            self._id_state.setText("An id is 1–80 letters, digits, and . _ - : "
                                   "(no spaces).")
            return
        warning = (f"Change this machine's id from {current} to {new}?\n\n"
                   "If another machine already uses this id, the hub can't tell "
                   "the two apart: their edits overwrite each other and no "
                   "conflict is ever shown.")
        seen = next((m for m in (sync.last.machines if sync.last else [])
                     if m.get("id") == new), None)
        if seen is not None:
            warning += (f"\n\nThe hub already knows {new} as "
                        f"\u201c{seen.get('name') or new}\u201d, last seen "
                        f"{_ago_ms(seen.get('last_seen'))}. Only go ahead if that "
                        "was this machine (before a reinstall) and it's no longer "
                        "running anywhere else.")
        answer = QMessageBox.warning(
            self, "Change machine id", warning,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            self._id_edit.setText(current)
            return
        sync.set_machine_id(new)
        self._id_state.setText(f"Now {new}. The hub will list {current} as a "
                               "machine that stopped checking in.")
        self._fill_sync_form()

    def _on_sync_progress(self, _text: str) -> None:
        self._draw_sync()

    def _sync_now(self) -> None:
        sync.sync_now()

    def _resend(self) -> None:
        """Push current state to every connected display, now."""
        webcast.push()
        self._refresh_btn.setText("Sent")
        QTimer.singleShot(1200, self._reset_resend)

    def _reset_resend(self) -> None:
        self._refresh_btn.setText("Resend to all displays")

    def _check_relay(self) -> None:
        """Pull now through the socket if it is up; ask for counters over HTTP either way."""
        nexus.relay.refresh()
        nexus.relay_stats()

    # ── Drawing ──────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._draw_relay()
        self._draw_feed()
        self._draw_displays()
        self._draw_leds()
        self._draw_sync()
        self._draw_recent()

    def _draw_relay(self) -> None:
        prefs = nexus_settings.load()
        tele = nexus.relay.telemetry()
        state = tele["state"]
        self._relay_dot.set_color(_RELAY_COLOR.get(state, brand.STATUS_IDLE))
        self._check_btn.setEnabled(nexus.relay_configured
                                   and bool(nexus.event_key))

        if not prefs["relay_enabled"]:
            self._relay_head.setText("Off — switched off on the Event Feed panel.")
            self._relay_link.setText("The live snapshot is polled instead.")
            self._relay_side.setText("")
            return
        if not nexus.relay_configured:
            self._relay_head.setText("Not set up — no relay token on this machine.")
            self._relay_link.setText(
                f"{prefs['relay_url']}. Paste the token on Event Feed → "
                "Nexus access (admin).")
            self._relay_side.setText("")
            return
        if not nexus.event_key:
            self._relay_head.setText("Idle — no event is set.")
            self._relay_link.setText(prefs["relay_url"])
            self._relay_side.setText("")
            return

        if state == "live":
            head = f"Live — {nexus.event_key}, connected {_ago(tele['uptime_s'])}"
        elif state == "connecting":
            head = "Connecting…"
        else:
            retry = tele["retry_in_ms"]
            head = ("Reconnecting"
                    + (f" in {retry / 1000:.0f}s" if retry is not None else "…"))
        self._relay_head.setText(head)

        link = [tele["url"]]
        link.append(f"{tele['messages']} messages, {_bytes(tele['bytes'])} · "
                    f"{tele['snapshots']} snapshots pushed · last message "
                    f"{_ago(tele['last_message_s'])}"
                    + (" ago" if tele["last_message_s"] is not None else ""))
        link.append(f"{tele['attempts']} connection attempts, "
                    f"{tele['reconnects']} reconnects this session")
        if tele["last_error"] and state != "live":
            link.append(f"Last error: {tele['last_error']}")
        self._relay_link.setText("\n".join(link))

        stats = tele["relay"]
        if not stats:
            self._relay_side.setText(
                "No counters from the relay yet — they arrive with the first "
                "message, or press Ask the relay now.")
            return
        side = [
            f"Nexus → relay: {stats.get('webhooks', 0)} webhooks — "
            f"{stats.get('accepted', 0)} newer, {stats.get('stale', 0)} stale — "
            f"last {_ago_ms(stats.get('lastWebhookAt'))}",
            f"Relay → Nexus: {stats.get('pulls', 0)} pulls, "
            f"{stats.get('pullFailures', 0)} failed — last "
            f"{_ago_ms(stats.get('lastPullAt'))}"
            + (f": {stats.get('lastPullResult')}"
               if stats.get("lastPullResult") else ""),
            f"Relay → displays: {stats.get('subscribers', 0)} subscribed · "
            f"held data as of {self._clock(stats.get('dataAsOfTime'))} "
            f"via {stats.get('lastSource') or '—'}",
        ]
        # `refused` counts for the life of the event's room, so one refusal
        # (say, while the relay's secret was still a placeholder) would warn
        # forever. It's only a live problem if no good webhook came after it.
        refused_at = stats.get("lastRefusedAt") or 0
        if stats.get("refused") and refused_at > (stats.get("lastWebhookAt") or 0):
            side.append(
                f"Webhooks are being refused (last {_ago_ms(refused_at)}): the "
                "token Nexus sends doesn't match the relay's. Nothing on this "
                "machine causes or fixes it, and the data stays current through "
                "the relay's 30s pull. Fix it on the relay — see "
                "nexus-relay/README.md, Registering with Nexus.")
        elif stats.get("refused"):
            side.append(
                f"{stats['refused']} webhook(s) refused earlier, last "
                f"{_ago_ms(refused_at)}; webhooks have been accepted since.")
        elif not stats.get("webhooks") and stats.get("pulls"):
            side.append("No webhooks have arrived for this event — the data is "
                        "current (the relay is pulling every 30s), but the "
                        "webhook is not registered at frc.nexus/api for it.")
        age = tele["relay_age_s"]
        side.append(f"(counters as of {_ago(age)} ago)" if age is not None else "")
        self._relay_side.setText("\n".join(l for l in side if l))

    def _draw_feed(self) -> None:
        if not nexus.event_key:
            self._feed.setText("No event set.")
            return
        lines = []
        age = nexus.age_s()
        src = nexus.status_source or "—"
        lines.append(f"Live snapshot: {src}"
                     + (f" · Nexus built it {_ago(age)} ago" if age is not None
                        else " · none held yet"))
        sources = nexus.sources()
        by = [f"{name} via {sources[job]}" for job, name in _JOBS[1:]
              if job in sources]
        if by:
            lines.append("Slow data: " + ", ".join(by))
        prefs = nexus_settings.load()
        if nexus.pushed_live:
            lines.append("Direct polling: off — the relay is pushing.")
        elif nexus.polling:
            lines.append(f"Polling: every {prefs['poll_interval_s']}s "
                         "(the relay is not pushing).")
        else:
            lines.append("Polling: not running.")
        if prefs["last_poll"]:
            lines.append(f"Last poll {prefs['last_poll']} UTC — "
                         f"{prefs['last_result']}")
        self._feed.setText("\n".join(lines))

    def _draw_displays(self) -> None:
        prefs = webcast_settings.load()
        if not webcast.listening:
            self._server_line.setText(
                "The pit network is off. Turn a screen on to the pit network "
                "from its own settings page, under Screens.")
            self._live.setText("No displays can connect while it is off.")
            self._refresh_btn.setEnabled(False)
            return

        self._refresh_btn.setEnabled(True)
        host = lan_address()
        published = ", ".join(SCREEN_LABELS.get(s, s)
                              for s in webcast.published()) or "none"
        self._server_line.setText(
            f"Serving on {host}:{webcast.port} — pages — and "
            f"{host}:{webcast.socket_port} — live data.\n"
            f"Published: {published}.\n"
            f"Bound to {prefs['bind']}"
            + ("  (this machine only; no other display can reach it)"
               if prefs["bind"] == "127.0.0.1" else "  (the pit network)"))

        live = webcast.telemetry().get("live", [])
        if not live:
            self._live.setText(
                "Nothing connected. On each display, open "
                f"http://{host}:{webcast.port}/ and pick a screen.")
            return
        rows = []
        for row in live:
            name = SCREEN_LABELS.get(row["screen"], row["screen"])
            rows.append(
                f"{name}  ·  {row['address']}  ·  connected "
                f"{_ago(row['uptime_s'])}  ·  {row['messages']} updates, "
                f"{_bytes(row['bytes'])}  ·  last send "
                f"{row['last_ms']:.1f} ms")
        self._live.setText("\n".join(rows))

    def _draw_leds(self) -> None:
        """
        The Arduino link, in two halves: what this machine measures (every
        firmware), and what the controller reports about itself (fw 2.5+,
        OP_STATUS). See `LinkStats` in app/leds/link.py and `sendStatus()` in
        firmware/pit_leds/pit_leds.ino.
        """
        t = leds.telemetry()
        if not leds.connected:
            self._led_dot.set_color(brand.STATUS_IDLE)
            self._led_head.setText("Not connected")
            self._leds.setText(f"{leds.status}\n{t['attempts']} connection "
                               f"attempts, {t['reconnects']} reconnects this session.")
            return

        info = leds.device
        dev = t["device"]
        findings = self._led_findings(t, dev)
        drift = self._led_drift(dev)
        if drift:
            findings.insert(0, drift)
        self._led_dot.set_color(brand.STATUS_PENDING if findings else brand.STATUS_ONLINE)
        self._led_head.setText(
            f"Connected on {leds.port_name or '?'}"
            + (f" · {info.describe()}" if info else "")
            + (f" · up {_ago(t['connected_s'])}" if t["connected_s"] is not None else ""))

        def ms(v):
            return "—" if v is None else f"{v:.1f} ms"

        lines = ["THIS MACHINE → CONTROLLER"]
        lines.append(f"Last heard {_ago(t['last_heard_s'])} ago · "
                     f"{t['cmd_per_s']:.1f} commands/s over the last 10 s")
        lines.append(f"Command delivered (queued → acknowledged, retries included): "
                     f"median {ms(t['deliver_p50_ms'])}, 95% {ms(t['deliver_p95_ms'])}, "
                     f"worst {ms(t['deliver_max_ms'])}")
        lines.append(f"Round trip per frame: median {ms(t['rtt_p50_ms'])}, "
                     f"95% {ms(t['rtt_p95_ms'])} · queued before sending: median "
                     f"{ms(t['wait_p50_ms'])}, 95% {ms(t['wait_p95_ms'])}")
        lines.append(f"{t['delivered']:,} commands delivered · {t['retries']:,} resends · "
                     f"{t['superseded']} replaced by a newer one · {t['unanswered']} "
                     f"given up · {t['probes_lost']} heartbeats unanswered · "
                     f"{t['naks']} rejected · {t['resyncs']} resyncs")
        lines.append(f"{t['sent']:,} frames sent ({_bytes(t['bytes_out'])}) · "
                     f"{t['acks']:,} acknowledged · {t['shed']} dropped from the queue")

        lines.append("")
        lines.append("THE CONTROLLER'S OWN VIEW")
        if dev is None:
            fw = info.version if info else "?"
            lines.append(f"Not available: firmware {fw} doesn't report it. Flash "
                         "firmware/pit_leds (2.5 or newer) for strip-write "
                         "timing, command-to-visible latency and receive errors.")
            switch_name, switch_age = t["switch"], t["switch_s"]
        else:
            us = lambda v: f"{v / 1000:.2f} ms"
            lines.append(f"Up {_ago(dev['uptime_ms'] / 1000)} · {dev['free_ram']} B "
                         f"RAM free · reported {_ago(dev['age_s'])} ago"
                         + (f" · rebooted {t['reboots']}× while connected"
                            if t["reboots"] else ""))
            if "frames_per_s" in dev:
                lines.append(f"Receiving {dev['frames_per_s']:.1f} frames/s "
                             f"({dev['rx_bytes_per_s']:.0f} B/s) · redrawing "
                             f"{dev['shows_per_s']:.1f} times/s")
            lines.append(f"Strip write (interrupts off, serial deaf): last "
                         f"{us(dev['show_us'])}, worst {us(dev['show_max_session_us'])}")
            lines.append(f"Longest the serial port went unread: "
                         f"{us(dev['gap_max_session_us'])}")
            if dev["cmd_us"] or dev["cmd_max_session_us"]:
                lines.append(f"Command received → on the strips: last {us(dev['cmd_us'])}, "
                             f"worst {us(dev['cmd_max_session_us'])}")
            else:
                lines.append("Command received → on the strips: no command has "
                             "reached the strips since the controller started")
            lines.append(f"Receive errors since boot: {dev['crc_errors']} bad checksum · "
                         f"{dev['decode_errors']} broken frames · {dev['overruns']} "
                         f"overruns · {dev['unknown_ops']} unknown commands · "
                         f"{dev['fallbacks']} watchdog fallbacks")
            if dev.get("cap_scale") is not None:
                lines.append(
                    f"Centre power budget: "
                    + ("not needed right now" if dev["cap_scale"] >= 1.0 else
                       f"holding the centre at {dev['cap_scale']:.0%} of this look's "
                       f"brightness")
                    + f" · {dev['capped_frames']:,} frames capped since boot")
            running = _MODE_NAMES.get(dev["mode"], f"mode {dev['mode']}")
            lines.append(f"Running: {running} at brightness {dev['brightness']}"
                         + (" · blanked (off)" if dev["blanked"] else ""))
            switch_name, switch_age = dev["switch"], dev["age_s"]

        switch = {"HOST": "middle — the app is in charge",
                  "WHITE": "up — white work light (app overridden)",
                  "RED": "down — solid red (app overridden)"}.get(switch_name)
        lines.append(f"Switch: {switch}" + (f", reported {_ago(switch_age)} ago"
                                            if switch_age is not None else "")
                     if switch else
                     "Switch: not reported since connecting — firmware older "
                     "than 2.5 only reports it when it moves")

        if leds.overridden:
            look = f"{config.mode} override"
        elif leds.alert is not None:
            look = f"alert: {leds.alert.label or 'queue call'}"
        else:
            look = "white work light" if leds.white else f"{leds.preset_key} look"
        lines.append(f"Showing: {look if leds.enabled else 'off (kill switch)'} · "
                     f"brightness {leds.brightness}")

        if findings:
            lines.append("")
            lines.extend(findings)
        if t["logs"]:
            lines.append("Controller says: " + " · ".join(
                f"{text} ({_ago(time.time() - at)} ago)" for at, text in t["logs"][:3]))
        self._leds.setText("\n".join(lines))

    @staticmethod
    def _led_drift(dev: dict | None) -> str:
        """
        Is the controller running what the app asked for? A lost SET_MODE or
        SET_BRIGHT leaves the strips on the old look while the app believes
        otherwise. Only judged on a report the controller produced after the
        app's last output command, so a command still in flight never counts.
        """
        if (dev is None or not dev.get("after_last_command") or not leds.enabled
                or leds.alert is not None or leds.overridden or dev["switch"] != "HOST"):
            return ""
        # The firmware caps brightness (MAX_BRIGHTNESS 200 in pit_leds.ino);
        # a request above it is clamped, not lost.
        wanted = (int(leds.mode), min(leds.brightness, 200))
        actual = (dev["mode"], dev["brightness"])
        if actual == wanted:
            return ""
        return (f"The strips aren't showing what the app asked for: the controller "
                f"is running {_MODE_NAMES.get(actual[0], actual[0])} at "
                f"{actual[1]}, the app wants {_MODE_NAMES.get(wanted[0], wanted[0])} "
                f"at {wanted[1]}. Commands were lost on the way in, and the app "
                f"doesn't resend them.")

    @staticmethod
    def _led_findings(t: dict, dev: dict | None) -> list[str]:
        """Only what somebody should act on, each with the likely fix."""
        out = []
        if t["last_heard_s"] is not None and t["last_heard_s"] > 3.0:
            out.append(f"Silent for {_ago(t['last_heard_s'])}: the heartbeat isn't "
                       "being answered. Check the USB cable and the controller's power.")
        if t["naks"] and not t["retries"]:
            out.append("Frames are being rejected as corrupted without any being "
                       "lost to strip writes — that's the cable or USB port.")
        if t["unanswered"] or t["shed"]:
            out.append("Commands were given up on after repeated resends, or "
                       "dropped: the link is failing (cable, port, or the "
                       "controller stuck). The app will resync once it recovers.")
        if t["delivered"] >= 20 and t["retries"] > t["delivered"]:
            out.append("More resends than commands: most first attempts are lost. "
                       "Expected only with firmware older than 2.7 during an "
                       "animated look — flash firmware/pit_leds.")
        if t["wait_p95_ms"] is not None and t["wait_p95_ms"] > 5:
            out.append(f"Commands wait up to {t['wait_p95_ms']:.0f} ms in this "
                       "machine's queue before they're even sent. That's host-side "
                       "latency, not the Arduino.")
        if t["reboots"]:
            out.append(f"The controller rebooted {t['reboots']}× while connected: "
                       "usually a brown-out when the strips draw too much from its supply.")
        return out

    def _draw_sync(self) -> None:
        state = sync.state()
        prefs = sync.prefs
        rep = sync.last
        colour = {"ok": brand.STATUS_ONLINE, "syncing": brand.STATUS_PENDING,
                  "waiting": brand.STATUS_PENDING, "error": brand.STATUS_FAULT
                  }.get(state, brand.STATUS_IDLE)
        self._sync_dot.set_color(colour)
        self._sync_btn.setEnabled(sync.enabled and not sync.busy)
        self._draw_sync_form()

        if state == "unconfigured":
            self._sync_head.setText("Not set up on this machine")
            self._sync.setText(
                "Paste the sync token below (admin), or import a pit setup "
                "file that carries it (Event Feed → Import setup file…). "
                "Until then this machine's settings and logs stay here.")
            return
        if state in ("off", "quiet"):
            self._sync_head.setText("Off on this machine")
            why = ("PIT_SYNC_QUIET is set" if state == "quiet"
                   else "turned off below, admin")
            self._sync.setText(f"{prefs['machine_name']} is not syncing ({why}).")
            return

        if sync.busy:
            head = sync.activity or "Syncing…"
        elif rep is None:
            head = "Starting…"
        elif rep.ok:
            head = f"In step · synced {_ago(time.time() - sync.last_at)} ago"
        else:
            head = rep.errors[0]
        self._sync_head.setText(head)

        lines = [f"This machine: {prefs['machine_name']}  ({prefs['machine_id']})",
                 f"Hub: {prefs['url']}", transfer.describe()]
        verdict = _verdict(prefs["machine_id"])
        if verdict:
            lines.append(verdict)
        if rep is not None:
            waiting = []
            if rep.outbox:
                waiting.append(f"{rep.outbox} change{'s' if rep.outbox != 1 else ''} to send")
            if rep.waiting:
                waiting.append(f"{rep.waiting} file{'s' if rep.waiting != 1 else ''} to download")
            if rep.pending:
                waiting.append(f"{rep.pending} waiting on something this machine "
                               "doesn't have (a track, a parent)")
            held = rep.held_uploads + rep.held_downloads
            if held:
                waiting.append(f"{held} team file{'s' if held != 1 else ''} held for "
                               "the night window")
            if rep.open_requests:
                waiting.append(f"{rep.open_requests} asked of home, not answered yet")
            lines.append("Waiting: " + (", ".join(waiting) if waiting else "nothing"))
            if rep.adopted:
                lines.append(f"First sync: took the team's version of {rep.adopted} "
                             "setting(s) this machine had from install")
            lines.append(f"Last cycle: {rep.pushed} sent, {rep.pulled} received"
                         + (f", {_bytes(rep.uploaded_bytes)} up" if rep.uploaded_bytes else "")
                         + (f", {_bytes(rep.downloaded_bytes)} down" if rep.downloaded_bytes else "")
                         + f" · {rep.seconds:.1f}s · at {rep.cursor} of {rep.head}")
            others = [m for m in rep.machines if m.get("id") != prefs["machine_id"]]
            for m in others[:6]:
                role = " (home)" if m.get("role") == "home" else ""
                lines.append(f"  {m.get('name') or m.get('id')}{role} · last seen "
                             f"{_ago_ms(m.get('last_seen'))}")
        for when, line in sync.history[:4]:
            lines.append(f"{self._clock(when * 1000)}  {line}")
        self._sync.setText("\n".join(lines))

    def _draw_recent(self) -> None:
        lines: list[tuple[float, str]] = []
        for row in leds.telemetry()["drops"]:
            lines.append((row["ended_at"],
                          f"LED controller  ·  dropped {_ago(time.time() - row['ended_at'])} "
                          f"ago after {_ago(row['uptime_s'])} — {row['reason']}"))
        for row in nexus.relay.telemetry()["history"]:
            lines.append((row["ended_at"],
                          f"Relay link  ·  dropped {_ago(time.time() - row['ended_at'])} "
                          f"ago after {_ago(row['uptime_s'])} — {row['reason']}"))
        if webcast.listening:
            for row in webcast.telemetry().get("recent", []):
                name = SCREEN_LABELS.get(row["screen"], row["screen"])
                ended = row.get("ended_at", time.time())
                when = _ago(time.time() - ended)
                if row.get("refused"):
                    text = (f"{row['address']} asked for {name} {when} ago — "
                            f"refused: {row['refused']}")
                else:
                    text = (f"{name}  ·  {row['address']}  ·  dropped {when} ago "
                            f"after {_ago(row['uptime_s'])}, "
                            f"{row['messages']} updates")
                lines.append((ended, text))
        lines.sort(key=lambda t: t[0], reverse=True)
        self._recent.setText("\n".join(t for _, t in lines[:8])
                             or "Nothing has dropped this session.")

    @staticmethod
    def _clock(ms) -> str:
        if not ms:
            return "—"
        return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc) \
            .astimezone().strftime("%H:%M:%S")
