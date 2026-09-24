"""
Event Feed panel — the operator's side of `app/nexus/`.

The questions, in the order somebody at the pit laptop asks them: **is the
feed live**, **which event**, **where is our next match**, **what is being
said**, and then the plumbing — the relay, the fallback polling, and (admin)
the keys. How the links are *behaving* — counts, ages, drops — is on
Pit Systems → Telemetry, not here.

Drawn the way the Software Updates panel is: the feed state is a `StatusDot`
and white type, one `primary` button (Refresh) that drops to an outline when
the feed cannot run, everything else neutral. The alliance word — RED / BLUE — is a filled block
with white type, the alliance's own colour and never a red letterform.

**Our match is a mono block, not a widget tree.** It is five or six lines that
change every thirty seconds, and rebuilding a row of labels each poll is what
made the old checklist editor flicker. One label, one `setText`.

The attribution line is a condition of the API's use, and it is on this panel
because this is the surface that says where the data came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QMessageBox, QSizePolicy,
    QVBoxLayout, QWidget,
)

from app import brand, provision
from app.admin import admin
from app.config import config
from app.leds import leds
from app.nexus import api, nexus, settings
from app.nexus import alerts as alerts_mod
from app.nexus.alerts import alerts
from app.nexus.api import (
    ATTRIBUTION, InspectionState, Match, MatchState, when,
)
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, SelectableChip, StatusDot, eyebrow, mono_font,
)
from app.widgets.helpers import divider, label
from app.widgets.toggle_switch import ToggleSwitch

_STATE_COLOR = {
    "off":     brand.STATUS_IDLE,
    "idle":    brand.STATUS_IDLE,
    "polling": brand.WHITE,
    "live":    brand.STATUS_ONLINE,
    "error":   brand.STATUS_FAULT,
}

_POLL_CHOICES = (15, 30, 60)
_LOG_LINES = 6

_INSPECTION_WORD = {
    InspectionState.COMPLETE:     "passed",
    InspectionState.QUEUED:       "queued",
    InspectionState.IN_PROGRESS:  "in progress",
    InspectionState.REINSPECTION: "re-inspection",
    InspectionState.HOLD:         "on hold",
    InspectionState.NOT_STARTED:  "not started",
}


def _rel(ms: int | None) -> str:
    """`in 12 min` / `4 min ago` / `now`, or `—` for a null timestamp.

    Past a day it is the date: a snapshot that old is a demo event or a
    schedule from last season, and "20236 h ago" says nothing useful.
    """
    if ms is None:
        return "—"
    delta = (ms - datetime.now(tz=timezone.utc).timestamp() * 1000) / 1000
    if abs(delta) < 45:
        return "now"
    if abs(delta) >= 86_400:
        dt = when(ms)
        return dt.strftime("%b %d") if dt else "—"
    if abs(delta) < 60 * 90:
        unit = f"{int(round(abs(delta) / 60))} min"
    else:
        unit = f"{abs(delta) / 3600:.1f} h"
    return f"in {unit}" if delta > 0 else f"{unit} ago"


def _clock(ms: int | None) -> str:
    dt = when(ms)
    return dt.strftime("%H:%M") if dt else "—"


def _team_list(teams: list[str | None] | None) -> str:
    if teams is None:
        return "TBD"
    return " ".join(t or "—" for t in teams)


class NexusPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log: list[str] = []
        self._build()
        nexus.state_changed.connect(self._on_state)
        nexus.status_changed.connect(lambda _s: self._refresh_live())
        nexus.match_changed.connect(lambda _m: self._refresh_live())
        nexus.pits_changed.connect(lambda _p: self._refresh_live())
        nexus.inspection_changed.connect(lambda _i: self._refresh_live())
        nexus.alliances_changed.connect(lambda _a: self._refresh_live())
        nexus.announcements_changed.connect(lambda _a: self._refresh_live())
        nexus.parts_requests_changed.connect(lambda _p: self._refresh_live())
        nexus.events_changed.connect(self._on_events)
        nexus.event_key_changed.connect(lambda _k: self._refresh_event())
        nexus.busy_changed.connect(lambda _b: self._on_state(nexus.state))
        nexus.log.connect(self._on_log)
        alerts.log.connect(self._on_log)
        alerts.banner_changed.connect(lambda _b: self._refresh_alerts())
        nexus.relay_changed.connect(self._refresh_relay)
        admin.lock_state_changed.connect(self._apply_lock)
        config.team_changed.connect(self._on_team_changed)
        self._apply_lock(admin.unlocked)
        self._refresh_all()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self):
        accent = config.active_team.primary_color
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Feed status ───────────────────────────────────────────────────
        self._card = RoundedFrame(fill=brand.CARBON_SURF2,
                                  border=brand.CARBON_LINE, radius=brand.R_BTN)
        card = QVBoxLayout(self._card)
        card.setContentsMargins(14, 12, 14, 12)
        card.setSpacing(8)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._dot = StatusDot()
        status_row.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._status_lbl = label("", "stat_value")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                                       QSizePolicy.Policy.Preferred)
        self._status_lbl.setMinimumWidth(0)
        status_row.addWidget(self._status_lbl, stretch=1)
        card.addLayout(status_row)
        root.addWidget(self._card)
        root.addSpacing(12)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._refresh_btn = RoundedButton("Refresh now", variant="primary",
                                          accent=accent)
        self._refresh_btn.clicked.connect(nexus.refresh)
        actions.addWidget(self._refresh_btn)
        actions.addStretch()
        root.addLayout(actions)
        root.addSpacing(6)
        attrib = label(f"{ATTRIBUTION}. Match times are only accurate at events "
                       "that queue with Nexus.", "stat_label")
        attrib.setWordWrap(True)
        root.addWidget(attrib)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Event ─────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Event"))
        root.addSpacing(8)
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("event key — 2026casf, or your demo key")
        self._key_edit.returnPressed.connect(self._use_key)
        key_row.addWidget(self._key_edit, stretch=1)
        use_btn = RoundedButton("Use", variant="secondary")
        use_btn.setFixedWidth(80)
        use_btn.clicked.connect(self._use_key)
        key_row.addWidget(use_btn)
        off_btn = RoundedButton("Off", variant="ghost")
        off_btn.setFixedWidth(70)
        off_btn.clicked.connect(lambda: self._apply_key(""))
        key_row.addWidget(off_btn)
        root.addLayout(key_row)
        root.addSpacing(8)
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self._events_combo = QComboBox()
        self._events_combo.setPlaceholderText("Active events on Nexus…")
        self._events_combo.activated.connect(self._pick_event)
        pick_row.addWidget(self._events_combo, stretch=1)
        list_btn = RoundedButton("List events", variant="secondary")
        list_btn.setFixedWidth(120)
        list_btn.clicked.connect(nexus.refresh_events)
        pick_row.addWidget(list_btn)
        root.addLayout(pick_row)
        root.addSpacing(6)
        self._event_lbl = label("", "stat_label")
        self._event_lbl.setWordWrap(True)
        root.addWidget(self._event_lbl)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Our team, live ────────────────────────────────────────────────
        self._team_eyebrow = eyebrow("Team")
        root.addWidget(self._team_eyebrow)
        root.addSpacing(8)
        # No `stat_value` object name: that QSS rule sets the display face and
        # would override the mono font, which is the point of this block.
        self._live_lbl = label("")
        self._live_lbl.setFont(mono_font(15))
        self._live_lbl.setStyleSheet(f"color: {brand.WHITE}; background: transparent;")
        self._live_lbl.setWordWrap(True)
        self._live_lbl.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self._live_lbl)
        root.addSpacing(14)

        root.addWidget(eyebrow("Announcements & parts requests"))
        root.addSpacing(8)
        self._board_lbl = label("", "stat_label")
        self._board_lbl.setWordWrap(True)
        root.addWidget(self._board_lbl)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Alerts ────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Alerts"))
        root.addSpacing(10)
        al = QHBoxLayout()
        al.setSpacing(12)
        al_text = label("Strips and overhead banner on queue and inspection",
                        "stat_value")
        al_text.setStyleSheet("font-size: 15px;")
        al.addWidget(al_text, stretch=1)
        self._alerts_toggle = ToggleSwitch()
        self._alerts_toggle.toggled.connect(alerts.set_enabled)
        al.addWidget(self._alerts_toggle, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(al)
        root.addSpacing(10)
        tests = QHBoxLayout()
        tests.setSpacing(8)
        for text, kind in (("Test first queue", alerts_mod.FIRST_QUEUE),
                           ("Test second queue", alerts_mod.SECOND_QUEUE),
                           ("Test inspection", alerts_mod.INSPECTION_PASSED)):
            btn = RoundedButton(text, variant="secondary")
            btn.clicked.connect(lambda _=False, k=kind: alerts.test(k))
            tests.addWidget(btn)
        clear_btn = RoundedButton("Clear", variant="ghost")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(alerts.clear)
        tests.addWidget(clear_btn)
        tests.addStretch()
        root.addLayout(tests)
        root.addSpacing(6)
        self._alert_lbl = label("", "stat_label")
        self._alert_lbl.setWordWrap(True)
        root.addWidget(self._alert_lbl)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Relay ─────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Relay"))
        root.addSpacing(10)
        rl = QHBoxLayout()
        rl.setSpacing(12)
        rl_text = label("Live updates pushed through the relay", "stat_value")
        rl_text.setStyleSheet("font-size: 15px;")
        rl.addWidget(rl_text, stretch=1)
        self._relay_toggle = ToggleSwitch()
        self._relay_toggle.toggled.connect(self._set_relay_enabled)
        rl.addWidget(self._relay_toggle, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(rl)
        root.addSpacing(8)
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self._relay_url_edit = QLineEdit()
        self._relay_url_edit.setPlaceholderText(settings.DEFAULT_RELAY_URL)
        self._relay_url_edit.returnPressed.connect(self._save_relay_url)
        url_row.addWidget(self._relay_url_edit, stretch=1)
        url_btn = RoundedButton("Save", variant="secondary")
        url_btn.setFixedWidth(90)
        url_btn.clicked.connect(self._save_relay_url)
        url_row.addWidget(url_btn)
        root.addLayout(url_row)
        root.addSpacing(6)
        relay_row = QHBoxLayout()
        relay_row.setSpacing(10)
        self._relay_dot = StatusDot()
        relay_row.addWidget(self._relay_dot, alignment=Qt.AlignmentFlag.AlignTop)
        self._relay_lbl = label("", "stat_label")
        self._relay_lbl.setWordWrap(True)
        relay_row.addWidget(self._relay_lbl, stretch=1)
        root.addLayout(relay_row)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Polling ───────────────────────────────────────────────────────
        root.addWidget(eyebrow("Fallback polling"))
        root.addSpacing(10)
        auto = QHBoxLayout()
        auto.setSpacing(12)
        auto_text = label("Poll when the relay is not pushing", "stat_value")
        auto_text.setStyleSheet("font-size: 15px;")
        auto.addWidget(auto_text, stretch=1)
        self._auto = ToggleSwitch()
        self._auto.toggled.connect(nexus.set_auto_poll)
        auto.addWidget(self._auto, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(auto)
        root.addSpacing(10)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._interval_chips: dict[int, SelectableChip] = {}
        for secs in _POLL_CHOICES:
            chip = SelectableChip(f"{secs}s")
            chip.setFixedWidth(72)
            chip.clicked.connect(lambda _=False, s=secs: self._set_interval(s))
            self._interval_chips[secs] = chip
            chips.addWidget(chip)
        chips.addStretch()
        root.addLayout(chips)
        root.addSpacing(8)
        self._poll_lbl = label("", "stat_label")
        self._poll_lbl.setWordWrap(True)
        root.addWidget(self._poll_lbl)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Access (admin) ────────────────────────────────────────────────
        self._keys_block = QWidget()
        kb = QVBoxLayout(self._keys_block)
        kb.setContentsMargins(0, 0, 0, 0)
        kb.setSpacing(0)
        kb.addWidget(eyebrow("Nexus access"))
        kb.addSpacing(8)
        keys_help = label(
            "The relay token is all a pit machine needs — it is the relay's "
            "CLIENT_TOKEN, and the relay holds the Nexus key itself. An API "
            "key from frc.nexus/api is optional: with one, this machine can "
            "still poll Nexus directly if the relay is ever unreachable. Both "
            "go to the secret folder beside the database, never the database.",
            "stat_label")
        keys_help.setWordWrap(True)
        kb.addWidget(keys_help)
        kb.addSpacing(10)
        self._token_edit, self._token_state = self._secret_row(
            kb, "Relay token", self._save_token, self._clear_token)
        kb.addSpacing(10)
        self._api_key_edit, self._api_key_state = self._secret_row(
            kb, "Nexus API key (optional, direct fallback)",
            self._save_api_key, self._clear_api_key)
        kb.addSpacing(14)
        setup_help = label(
            "Or move the whole setup as one file — token, event, relay — "
            "made on the machine that has them. A pit-setup.json dropped "
            "beside the database is also picked up at the next launch.",
            "stat_label")
        setup_help.setWordWrap(True)
        kb.addWidget(setup_help)
        kb.addSpacing(8)
        setup_row = QHBoxLayout()
        setup_row.setSpacing(8)
        import_btn = RoundedButton("Import setup file…", variant="secondary")
        import_btn.clicked.connect(self._import_setup)
        setup_row.addWidget(import_btn)
        export_btn = RoundedButton("Export setup file…", variant="secondary")
        export_btn.clicked.connect(self._export_setup)
        setup_row.addWidget(export_btn)
        setup_row.addStretch()
        kb.addLayout(setup_row)
        kb.addSpacing(4)
        self._setup_state = label("", "stat_label")
        self._setup_state.setWordWrap(True)
        kb.addWidget(self._setup_state)
        root.addWidget(self._keys_block)
        self._locked_note = label(
            "Nexus keys are admin-only — unlock with the Breakaway mark, "
            "top left.", "stat_label")
        self._locked_note.setWordWrap(True)
        root.addWidget(self._locked_note)
        root.addSpacing(16)
        root.addWidget(divider())
        root.addSpacing(16)

        # ── Log ───────────────────────────────────────────────────────────
        root.addWidget(eyebrow("Feed log"))
        root.addSpacing(8)
        self._log_lbl = label("—", "stat_label")
        self._log_lbl.setFont(mono_font())
        self._log_lbl.setWordWrap(True)
        root.addWidget(self._log_lbl)

    def _secret_row(self, layout: QVBoxLayout, placeholder: str, on_save, on_clear):
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText(placeholder)
        row.addWidget(edit, stretch=1)
        save_btn = RoundedButton("Save", variant="secondary")
        save_btn.setFixedWidth(90)
        save_btn.clicked.connect(on_save)
        row.addWidget(save_btn)
        clear_btn = RoundedButton("Clear", variant="ghost")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(on_clear)
        row.addWidget(clear_btn)
        layout.addLayout(row)
        layout.addSpacing(4)
        state = label("", "stat_label")
        state.setWordWrap(True)
        layout.addWidget(state)
        return edit, state

    # ── State ─────────────────────────────────────────────────────────────

    def _refresh_all(self):
        self._refresh_event()
        self._refresh_live()
        self._refresh_alerts()
        self._refresh_polling()
        self._refresh_relay()
        self._refresh_keys()
        self._on_state(nexus.state)

    def _on_state(self, state: str):
        self._status_lbl.setText(nexus.message or nexus.blocked_reason)
        self._status_lbl.setStyleSheet(
            f"color: {brand.WHITE}; background: transparent;")
        self._dot.set_color(_STATE_COLOR.get(state, brand.STATUS_IDLE))
        can = nexus.configured
        self._refresh_btn.setEnabled(can and not nexus.busy)
        self._refresh_btn.setText("Fetching…" if nexus.busy else "Refresh now")
        # A disabled `primary` is still a red block; a feed that cannot run
        # would spend the panel's one red on a button that does nothing.
        self._refresh_btn.set_variant("primary" if can else "secondary")

    def _refresh_event(self):
        key = nexus.event_key
        if self._key_edit.text().strip() != key:
            self._key_edit.setText(key)
        name = nexus.event_name()
        entry = nexus.events.get(key)
        if not key:
            self._event_lbl.setText("No event. The feed is off until one is set.")
        elif entry:
            start = when(entry.start)
            end = when(entry.end)
            span = (f"{start:%b %d} – {end:%b %d}" if start and end else "")
            self._event_lbl.setText(f"{name}  ·  {span}".strip(" ·"))
        elif nexus.fake:
            self._event_lbl.setText(f"{key} — served from the bundled examples "
                                    "(PIT_NEXUS_FAKE=1).")
        else:
            self._event_lbl.setText(f"{key}. Press List events to see its name.")
        idx = self._events_combo.findData(key)
        self._events_combo.setCurrentIndex(idx)

    def _on_events(self, events: dict):
        self._events_combo.clear()
        for key, entry in sorted(events.items(), key=lambda kv: kv[1].start):
            start = when(entry.start)
            tag = " · live" if entry.is_live else ""
            self._events_combo.addItem(
                f"{entry.name}  ({key}{tag}"
                + (f", {start:%b %d}" if start else "") + ")", key)
        self._refresh_event()

    def _refresh_live(self):
        team = nexus.our_team
        self._team_eyebrow.setText(f"TEAM {team} AT THIS EVENT")
        status = nexus.status
        if status is None:
            self._live_lbl.setText("No snapshot yet.")
            self._board_lbl.setText("—")
            return

        lines: list[str] = []
        nq = status.now_queuing
        lines.append(f"Now queuing   {nq or '—'}")

        current = nexus.current_match()
        nxt = nexus.next_match()
        if nxt is not None:
            lines.extend(self._match_lines("Next match", nxt, team))
        elif current is not None:
            lines.extend(self._match_lines("Last match", current, team))
        else:
            ours = len(nexus.our_matches())
            lines.append("Next match    —" + ("  (not in the schedule)"
                                              if not ours else ""))

        pit = nexus.our_pit()
        insp = nexus.our_inspection()
        insp_txt = "—"
        if insp is not None:
            if insp.status is None:
                insp_txt = "passed" if insp.inspected else "not yet"
            else:
                insp_txt = _INSPECTION_WORD.get(insp.status, insp.status)
                if insp.queue_position is not None:
                    insp_txt += f"  #{insp.queue_position} in queue"
        lines.append(f"Pit           {pit or '—'}")
        lines.append(f"Inspection    {insp_txt}")

        alliance = nexus.our_alliance()
        if alliance is not None:
            slot = alliance.teams.index(team)
            role = ("captain", "1st pick", "2nd pick", "backup")[min(slot, 3)]
            lines.append(f"Alliance      #{alliance.number}  {role}  "
                         f"({_team_list(alliance.teams)})")

        brk = status.next_break()
        if brk is not None:
            lines.append(f"Next break    {brk[1]} after {brk[0].label}")

        html = "<br>".join(self._esc(l) for l in lines)
        self._live_lbl.setText(html)

        board: list[str] = []
        for a in status.announcements[-4:]:
            board.append(f"{_clock(a.posted)}  {a.text}")
        for p in status.parts_requests[-4:]:
            board.append(f"{_clock(p.posted)}  Team {p.requested_by} needs {p.parts}")
        self._board_lbl.setText("\n".join(board) if board else "Nothing posted.")

    def _match_lines(self, head: str, m: Match, team: str) -> list[str]:
        side = m.alliance_of(team) or "—"
        # A filled block with white type — never red letterforms on carbon.
        fill = brand.RED if side == "red" else brand.SKY
        side_html = (f'<span style="background:{fill};color:{brand.WHITE}">'
                     f'&nbsp;{side.upper()}&nbsp;</span>'
                     if side in ("red", "blue") else side)
        t = m.times
        out = [f"{head:<13} {m.label}  ·  {m.status}"
               + (f"  (replay of {m.replay_of})" if m.replay_of else ""),
               f"  alliance    @@{side_html}@@  with {_team_list(m.red_teams if side == 'red' else m.blue_teams)}",
               f"  vs          {_team_list(m.blue_teams if side == 'red' else m.red_teams)}"]
        rank = MatchState.rank(m.status)
        if rank < MatchState.rank(MatchState.NOW_QUEUING):
            out.append(f"  queue       {_clock(t.estimated_queue)}  {_rel(t.estimated_queue)}")
        if rank < MatchState.rank(MatchState.ON_DECK):
            out.append(f"  on deck     {_clock(t.estimated_on_deck)}  {_rel(t.estimated_on_deck)}")
        if rank < MatchState.rank(MatchState.ON_FIELD):
            out.append(f"  on field    {_clock(t.estimated_on_field)}  {_rel(t.estimated_on_field)}")
        out.append(f"  start       {_clock(t.estimated_start)}  {_rel(t.estimated_start)}")
        if t.actual_commit:
            out.append(f"  scored      {_clock(t.actual_commit)}")
        if m.break_after:
            out.append(f"  then        {m.break_after}")
        return out

    @staticmethod
    def _esc(line: str) -> str:
        """Escape for rich text, keeping the one `@@…@@` span raw."""
        parts = line.split("@@")
        out = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                out.append(part)
            else:
                out.append(part.replace("&", "&amp;").replace("<", "&lt;")
                           .replace(">", "&gt;").replace(" ", "&nbsp;"))
        return "".join(out)

    def _refresh_alerts(self):
        self._alerts_toggle.blockSignals(True)
        self._alerts_toggle.setChecked(alerts.enabled)
        self._alerts_toggle.blockSignals(False)
        b = alerts.banner
        fw = ("" if leds.supports_white or not leds.connected
              else " Controller firmware is older than 2.2, so the centre run "
                   "goes dark rather than white during a queue alert — reflash "
                   "firmware/pit_leds.")
        if b is None:
            self._alert_lbl.setText(
                "Nothing up. First queue = Now queuing, second queue = On deck: "
                "sides flash the alliance colour 2 s then steady 1 s, centre "
                "white. Inspection passed: sides green for 5 s. The overhead "
                "banner shows for 10 s either way." + fw)
        else:
            self._alert_lbl.setText(f"Up now: {b.headline} — {b.detail}." + fw)

    def _refresh_polling(self):
        prefs = settings.load()
        self._auto.blockSignals(True)
        self._auto.setChecked(bool(prefs["auto_poll"]))
        self._auto.blockSignals(False)
        for secs, chip in self._interval_chips.items():
            chip.set_active(secs == int(prefs["poll_interval_s"]))
        last = prefs["last_poll"]
        cadence = (f"Every {prefs['poll_interval_s']}s for the live snapshot, "
                   f"every {prefs['slow_poll_interval_s'] // 60} min for pits, "
                   "map, inspection, teams and alliances.")
        self._poll_lbl.setText(
            f"{cadence} Last poll {last} UTC — {prefs['last_result']}."
            if last else f"{cadence} Not polled yet.")

    def _refresh_relay(self):
        prefs = settings.load()
        self._relay_toggle.blockSignals(True)
        self._relay_toggle.setChecked(bool(prefs["relay_enabled"]))
        self._relay_toggle.blockSignals(False)
        if not self._relay_url_edit.hasFocus():
            self._relay_url_edit.setText(prefs["relay_url"])
        link = nexus.relay
        tele = link.telemetry()
        stats = tele["relay"]
        if not prefs["relay_enabled"]:
            color, text = brand.STATUS_IDLE, "Off. The live snapshot is polled."
        elif not api.relay_token():
            color, text = brand.STATUS_IDLE, (
                "No relay token on this machine — paste it under Nexus access "
                "(admin).")
        elif link.connected:
            color = brand.STATUS_ONLINE
            text = f"Live — {tele['snapshots']} snapshot(s) pushed this session."
            if stats:
                hook = stats.get("lastWebhookAt") or 0
                text += (f" The relay has had {stats.get('webhooks', 0)} webhook(s)"
                         + (f", the last {'just now' if _rel(hook) == 'now' else _rel(hook)}."
                            if hook else
                            " — none yet; it is pulling Nexus every 30s instead."))
        elif link.state == "off":
            color, text = brand.STATUS_IDLE, "Not connected — no event is set."
        else:
            color = brand.STATUS_PENDING
            text = "Reconnecting…"
            if tele["last_error"]:
                text += f" {tele['last_error']}"
            if nexus.polling:
                text += " Polling for the live snapshot meanwhile."
        self._relay_dot.set_color(color)
        self._relay_lbl.setText(text + "  Details: Pit Systems → Telemetry.")

    def _refresh_keys(self):
        self._token_state.setText(
            "A relay token is stored on this machine." if api.relay_token()
            else "No relay token — the relay cannot be used.")
        self._api_key_state.setText(
            "An API key is stored — direct polling is available as a fallback."
            if api.configured() else
            "No API key — fine while the relay works; there is no fallback "
            "without it.")

    def _on_log(self, line: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"{stamp}  {line}")
        self._log = self._log[-_LOG_LINES:]
        self._log_lbl.setText("\n".join(self._log))

    def _apply_lock(self, unlocked: bool):
        # Hidden, not disabled — the same line the update token draws.
        self._keys_block.setVisible(unlocked)
        self._locked_note.setVisible(not unlocked)

    def _on_team_changed(self, team):
        self._refresh_btn.set_accent(team.primary_color)
        self._refresh_live()

    # ── Actions ───────────────────────────────────────────────────────────

    def _use_key(self):
        self._apply_key(self._key_edit.text())

    def _apply_key(self, key: str):
        nexus.set_event_key(key)
        self._refresh_event()
        self._refresh_polling()

    def _pick_event(self, index: int):
        key = self._events_combo.itemData(index)
        if key:
            self._apply_key(str(key))

    def _set_interval(self, seconds: int):
        nexus.set_poll_interval(seconds)
        self._refresh_polling()

    def _set_relay_enabled(self, on: bool):
        nexus.set_relay(enabled=on)
        self._refresh_relay()

    def _save_relay_url(self):
        url = self._relay_url_edit.text().strip() or settings.DEFAULT_RELAY_URL
        nexus.set_relay(url=url)
        self._relay_url_edit.clearFocus()
        self._refresh_relay()

    def _save_api_key(self):
        value = self._api_key_edit.text().strip()
        if not value:
            return
        nexus.set_api_key(value)
        self._api_key_edit.clear()
        self._refresh_keys()
        self._refresh_polling()

    def _clear_api_key(self):
        nexus.set_api_key("")
        self._api_key_edit.clear()
        self._refresh_keys()

    def _save_token(self):
        value = self._token_edit.text().strip()
        if not value:
            return
        nexus.set_relay_token(value)
        self._token_edit.clear()
        self._refresh_keys()
        self._refresh_relay()

    def _clear_token(self):
        nexus.set_relay_token("")
        self._token_edit.clear()
        self._refresh_keys()
        self._refresh_relay()

    def _import_setup(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import pit setup file", "",
            "Pit setup (*.json);;All files (*)")
        if not path:
            return
        try:
            lines = provision.apply_file(Path(path))
        except provision.ProvisionError as exc:
            QMessageBox.warning(self, "Not applied", str(exc))
            return
        self._setup_state.setText("Imported: " + "; ".join(lines))
        # The service re-reads keys, relay and event; the panel follows its signals.
        nexus.set_event_key(settings.get("event_key"))
        nexus.set_relay()
        self._refresh_all()

    def _export_setup(self):
        setup = provision.current()
        if setup.empty:
            QMessageBox.information(
                self, "Nothing to export",
                "This machine has no keys and no event to put in a file.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export pit setup file", "pit-setup.json",
            "Pit setup (*.json)")
        if not path:
            return
        try:
            provision.write(setup, Path(path))
        except OSError as exc:
            QMessageBox.warning(self, "Could not write", str(exc))
            return
        self._setup_state.setText(
            f"Exported to {path} — it carries the keys in plain text. Copy it "
            "to the pit machine's data directory as pit-setup.json, or import "
            "it there; then delete it from the stick.")
