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
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app import brand
from app.config import SCREEN_LABELS
from app.leds import leds
from app.nexus import nexus
from app.nexus import settings as nexus_settings
from app.webcast import lan_address, webcast
from app.webcast import settings as webcast_settings
from app.widgets.brand_widgets import RoundedButton, StatusDot, eyebrow
from app.widgets.helpers import divider, label
from app.widgets.robot_panel import RobotLogPanel

_RELAY_COLOR = {
    "live":         brand.STATUS_ONLINE,
    "connecting":   brand.STATUS_PENDING,
    "reconnecting": brand.STATUS_PENDING,
    "off":          brand.STATUS_IDLE,
}

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
        self._leds = label("", "stat_label")
        self._leds.setWordWrap(True)
        root.addWidget(self._leds)
        root.addSpacing(16)
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
        if stats.get("refused"):
            side.append(
                f"{stats['refused']} webhook(s) REFUSED, last "
                f"{_ago_ms(stats.get('lastRefusedAt'))} — the Nexus-Token at "
                "frc.nexus/api does not match the relay's NEXUS_WEBHOOK_TOKEN.")
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
        if leds.connected:
            info = leds.device
            line = f"Connected on {leds.port_name or '?'}"
            if info:
                line += f" · {info.describe()}"
            # The link's own status usually says the same thing; only show it
            # when it adds something.
            if info and info.describe() not in leds.status:
                line += f"\n{leds.status}"
            self._leds.setText(line)
        else:
            self._leds.setText(f"Not connected — {leds.status}")

    def _draw_recent(self) -> None:
        lines: list[tuple[float, str]] = []
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
