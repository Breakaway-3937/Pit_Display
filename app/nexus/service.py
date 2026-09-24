"""
`_NexusService` — the singleton every screen reads the event through.

It owns one event key, one live `EventStatus` snapshot and the five slower
per-event fetches (pits, map, inspection, teams, alliances), and turns them
into the Qt signals the rest of the app is built on. Nothing else in the app
talks HTTP to Nexus.

    relay socket (push, ~instant) ─────────────────────┐
    live timer (30s, only while the socket is down) ──┤─▶ newer dataAsOfTime? ─▶ snapshot
    slow timer (5m) ─▶ pits · map · inspection · teams · alliances       │
                                                                          ▼
                       status_changed · match_changed · now_queuing_changed · …

**The relay is the front door; direct Nexus is the back one.** Every fetch
goes through `_tiered()`: the relay's mirrored API first
(`https://nexus.bh-stack.com/api/v1/…`, needing only the relay token), then
frc.nexus itself if this machine also has an API key. A 404 is an answer and
stops there; anything else — no route, a refused token, a relay with no key —
falls through to the next door.

**The live timer only runs while the relay socket is not delivering.** The
relay pushes each snapshot as it lands and pulls Nexus itself every 30s while
anyone is subscribed, so polling on top of that is the same request twice. When
the socket has been down for `fallback_after_s` the timer starts; the moment
it reconnects the timer stops.

**Newest `dataAsOfTime` wins, whichever way it arrived.** A push can land
between two polls and a poll between two pushes; every snapshot goes through
`_offer_status()`, which drops anything not newer than what is held.

**"Our" match is derived, never stored.** `next_match()` and `current_match()`
read `config.active_team` at call time, so switching the team on the control
screen switches every board without a refetch. `match_changed` fires when the
derived answer changes — label, status *or* estimated times — because a match
whose start moved by four minutes is news to a pit crew.

**One worker at a time, jobs queued.** Every fetch runs on a `QThread`; the
service never blocks the event loop and never runs two fetches at once, so a
slow event wifi cannot pile up requests. A job that is already queued is not
queued twice.

Turning the feed off is `set_event_key("")`: timers stop, the snapshot is
cleared, every `*_changed` signal fires with its empty value so a board showing
a match goes back to showing nothing rather than a match from the last event.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from app.config import config
from app.lazy_proxy import LazyProxy
from app.nexus import api, settings
from app.nexus.api import (
    Alliances, EventStatus, EventSummary, InspectionStatus, Match, MatchStatus,
    NexusError, PitMap,
)
from app.nexus.relay import RelayLink

# The first poll after startup. The control screen builds, the audience screens
# come up, and only then does the machine go and ask the internet anything.
_FIRST_POLL_MS = 3_000


class _Fetch(QThread):
    """One job — a name and a callable — on its own thread."""

    done = pyqtSignal(str, object)
    failed = pyqtSignal(str, str, object)       # job, message, HTTP code | None

    def __init__(self, job: str, fn: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._job = job
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._job, self._fn())
        except NexusError as exc:
            self.failed.emit(self._job, str(exc), exc.code)
        except Exception as exc:                        # pragma: no cover
            self.failed.emit(self._job, f"{type(exc).__name__}: {exc}", None)


class _NexusService(QObject):

    # ── Feed state ───────────────────────────────────────────────────────
    # "off" — no event key or no API key (message says which)
    # "idle" — configured, nothing fetched yet, polling off
    # "polling" — first fetch in flight
    # "live" — holding a snapshot
    # "error" — last fetch failed; the previous snapshot, if any, is kept
    state_changed = pyqtSignal(str)
    message_changed = pyqtSignal(str)
    event_key_changed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)

    # ── Data ─────────────────────────────────────────────────────────────
    status_changed = pyqtSignal(object)         # EventStatus | None
    match_changed = pyqtSignal(object)          # Match | None — ours, derived
    now_queuing_changed = pyqtSignal(object)    # str | None
    announcements_changed = pyqtSignal(list)    # list[Announcement]
    parts_requests_changed = pyqtSignal(list)   # list[PartsRequest]
    pits_changed = pyqtSignal(dict)             # {team: address}
    pit_map_changed = pyqtSignal(object)        # PitMap | None
    inspection_changed = pyqtSignal(dict)       # {team: InspectionStatus}
    teams_changed = pyqtSignal(list)            # list[str]
    alliances_changed = pyqtSignal(object)      # Alliances | None
    events_changed = pyqtSignal(dict)           # {key: EventSummary}
    relay_changed = pyqtSignal()                # socket state or relay counters moved
    log = pyqtSignal(str)                       # one line for the panel

    def __init__(self):
        super().__init__()
        self._client = api.make_client()
        self._state = "off"
        self._message = ""

        self._status: EventStatus | None = None
        self._pits: dict[str, str] = {}
        self._pit_map: PitMap | None = None
        self._inspection: dict[str, InspectionStatus] = {}
        self._teams: list[str] = []
        self._alliances: Alliances | None = None
        self._events: dict[str, EventSummary] = {}
        self._last_match_key: tuple | None = None

        self._worker: _Fetch | None = None
        self._queue: list[tuple[str, Callable[[], Any]]] = []
        # Which door the last answer to each job came through — "relay",
        # "direct" or "fake". The Telemetry panel's question, not the boards'.
        self._source: dict[str, str] = {}
        self._status_source = ""

        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self.poll)
        self._slow_timer = QTimer(self)
        self._slow_timer.timeout.connect(self.poll_slow)
        # Armed when the relay socket drops; if it has not come back when
        # this fires, the live snapshot is polled instead.
        self._fallback_timer = QTimer(self)
        self._fallback_timer.setSingleShot(True)
        self._fallback_timer.timeout.connect(self._on_fallback_due)

        self._relay = RelayLink(self)
        self._relay.status_received.connect(self._on_relay_status)
        self._relay.connection_changed.connect(self._on_relay_connection)
        self._relay.stats_received.connect(self._on_relay_stats)
        self._relay.telemetry_changed.connect(self.relay_changed)

        config.team_changed.connect(self._on_team_changed)

        prefs = settings.load()
        self._event_key: str = prefs["event_key"]
        # `PIT_NEXUS_QUIET=1` builds the service without touching a timer or a
        # socket — what --self-check wants, since it runs beside the live app
        # on the same machine and must not open a second relay socket or poll.
        self._quiet = os.environ.get("PIT_NEXUS_QUIET", "") not in ("", "0")
        if not self._quiet and self.configured:
            self._start_feed()
            QTimer.singleShot(_FIRST_POLL_MS, self.refresh)
        self._sync_state()

    # ── What is configured ───────────────────────────────────────────────

    @property
    def fake(self) -> bool:
        return isinstance(self._client, api.FakeClient)

    @property
    def event_key(self) -> str:
        return self._event_key

    @property
    def relay_configured(self) -> bool:
        """The relay is switched on, has an address, and this machine has its token."""
        if self.fake:
            return False
        prefs = settings.load()
        return bool(prefs["relay_enabled"] and prefs["relay_url"]
                    and api.relay_token())

    @property
    def has_key(self) -> bool:
        """Some door to Nexus is open: the relay, a direct key, or the fake."""
        return self.fake or self.relay_configured or api.configured()

    @property
    def configured(self) -> bool:
        return bool(self._event_key) and self.has_key

    @property
    def blocked_reason(self) -> str:
        if not self.has_key:
            return ("No way to reach Nexus from this machine. Paste the relay "
                    "token below (admin) — that is all a pit machine needs. A "
                    "Nexus API key works too, as a direct fallback.")
        if not self._event_key:
            return "No event selected. Enter the event key, or pick one from the list."
        return ""

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    @property
    def our_team(self) -> str:
        return str(config.active_team.number)

    # ── Held data ────────────────────────────────────────────────────────

    @property
    def status(self) -> EventStatus | None:
        return self._status

    @property
    def pits(self) -> dict[str, str]:
        return self._pits

    @property
    def pit_map(self) -> PitMap | None:
        return self._pit_map

    @property
    def inspection(self) -> dict[str, InspectionStatus]:
        return self._inspection

    @property
    def teams(self) -> list[str]:
        return self._teams

    @property
    def alliances(self) -> Alliances | None:
        return self._alliances

    @property
    def events(self) -> dict[str, EventSummary]:
        return self._events

    @property
    def relay(self) -> RelayLink:
        return self._relay

    @property
    def status_source(self) -> str:
        """How the held snapshot arrived: `relay push`, `relay`, `direct`, `fake`."""
        return self._status_source

    def sources(self) -> dict[str, str]:
        """The door each job's last answer came through."""
        return dict(self._source)

    def age_s(self) -> float | None:
        """Seconds since Nexus built the held snapshot, or None without one."""
        if self._status is None or not self._status.data_as_of:
            return None
        now_ms = datetime.now(tz=timezone.utc).timestamp() * 1000
        return max(0.0, (now_ms - self._status.data_as_of) / 1000)

    # ── Derived, for the active team ─────────────────────────────────────

    def our_matches(self) -> list[Match]:
        return self._status.matches_for(self.our_team) if self._status else []

    def next_match(self) -> Match | None:
        return self._status.next_for(self.our_team) if self._status else None

    def current_match(self) -> Match | None:
        return self._status.current_for(self.our_team) if self._status else None

    def now_queuing(self) -> str | None:
        return self._status.now_queuing if self._status else None

    def our_pit(self) -> str | None:
        return self._pits.get(self.our_team)

    def our_inspection(self) -> InspectionStatus | None:
        return self._inspection.get(self.our_team)

    def our_alliance(self):
        return self._alliances.alliance_of(self.our_team) if self._alliances else None

    def event_name(self) -> str:
        entry = self._events.get(self._event_key)
        return entry.name if entry else ""

    # ── Configuration ────────────────────────────────────────────────────

    def set_event_key(self, key: str) -> None:
        key = (key or "").strip()
        if key == self._event_key:
            return
        settings.save(event_key=key)
        self._event_key = key
        self._queue.clear()
        self._clear_event_data()
        self.event_key_changed.emit(key)
        self._reconfigure()

    def set_api_key(self, value: str) -> None:
        api.set_api_key(value)
        self._reconfigure()

    def set_relay_token(self, value: str) -> None:
        api.set_relay_token(value)
        self._reconfigure()

    def set_relay(self, *, enabled: bool | None = None,
                  url: str | None = None) -> None:
        changes: dict[str, Any] = {}
        if enabled is not None:
            changes["relay_enabled"] = bool(enabled)
        if url is not None:
            changes["relay_url"] = url
        settings.save(**changes)
        self._reconfigure()

    def set_auto_poll(self, on: bool) -> None:
        settings.save(auto_poll=bool(on))
        self._stop_timers()
        if self.configured and not self._quiet:
            self._start_feed()
            if on:
                self.poll()
        self._sync_state()

    def set_poll_interval(self, seconds: int) -> None:
        prefs = settings.save(poll_interval_s=int(seconds))
        if self._live_timer.isActive():
            self._live_timer.start(prefs["poll_interval_s"] * 1000)

    def _reconfigure(self) -> None:
        """Keys, event or relay changed: tear the feed down and bring it up again."""
        self._relay.stop()
        self._stop_timers()
        if self.configured and not self._quiet:
            self._start_feed()
        self._sync_state()
        if self.configured:
            self.refresh()

    # ── Actions ──────────────────────────────────────────────────────────

    def poll(self) -> None:
        """One live snapshot. Silently a no-op when not configured."""
        if not self.configured:
            return
        key = self._event_key
        self._enqueue("status", self._tiered(lambda c: c.event_status(key)))

    def poll_slow(self) -> None:
        """The five slower per-event endpoints."""
        if not self.configured:
            return
        key = self._event_key
        self._enqueue("pits", self._tiered(lambda c: c.pit_addresses(key)))
        self._enqueue("map", self._tiered(lambda c: c.pit_map(key)))
        self._enqueue("inspection", self._tiered(lambda c: c.inspection(key)))
        self._enqueue("teams", self._tiered(lambda c: c.teams(key)))
        self._enqueue("alliances", self._tiered(lambda c: c.alliances(key)))

    def refresh(self) -> None:
        """Everything, now — what the panel's Refresh button does."""
        if not self.configured:
            self._sync_state()
            return
        if self._status is None:
            where = "the relay" if self.relay_configured else "frc.nexus"
            self._set_state("polling", f"Fetching the event from {where}…")
        # With the socket up, the relay pulls Nexus and pushes the answer
        # down it; otherwise ask over HTTP, through whichever door is open.
        if not self._relay.refresh():
            self.poll()
        self.poll_slow()

    def refresh_events(self) -> None:
        """`GET /events` — the picker's list. Needs a door, not an event."""
        if not self.has_key:
            self._set_state("off", self.blocked_reason)
            return
        self._enqueue("events", self._tiered(lambda c: c.events()))

    def relay_stats(self) -> None:
        """Fetch the relay's counters over HTTP — for when the socket is down."""
        if not (self.relay_configured and self._event_key):
            return
        key = self._event_key
        client = self._relay_client()
        self._enqueue("relay", lambda: ("relay", client.relay_stats(key)))

    def shutdown(self) -> None:
        self._stop_timers()
        self._relay.stop()
        self._queue.clear()
        if self._worker is not None:
            self._worker.wait(3_000)

    # ── The doors ────────────────────────────────────────────────────────

    def _relay_client(self) -> api.RelayClient:
        return api.RelayClient(str(settings.get("relay_url")))

    def _doors(self) -> list[tuple[str, api.Client]]:
        """Every way to Nexus this machine has, front door first."""
        if self.fake:
            return [("fake", self._client)]
        doors: list[tuple[str, api.Client]] = []
        if self.relay_configured:
            doors.append(("relay", self._relay_client()))
        if api.configured():
            doors.append(("direct", self._client))
        return doors

    def _tiered(self, fetch: Callable[[api.Client], Any]) -> Callable[[], Any]:
        """
        A job that tries each door in turn and returns `(door, result)`.

        Resolved *now*, on the event loop, so the worker thread never reads
        settings or the secret folder. A 404 is Nexus's answer, whichever
        door carried it, and is not retried; anything else falls through.
        """
        doors = self._doors()

        def run():
            last: NexusError | None = None
            for name, client in doors:
                try:
                    return name, fetch(client)
                except NexusError as exc:
                    if exc.code == 404:
                        raise
                    last = exc
            raise last or NexusError("No way to reach Nexus from this machine.")
        return run

    # ── Applying data ────────────────────────────────────────────────────

    def _offer_status(self, status: EventStatus, source: str) -> bool:
        """Adopt `status` if it is newer than what is held. Returns whether it was."""
        if (status.event_key and self._event_key
                and status.event_key.lower() != self._event_key.lower()):
            self.log.emit(f"Ignored a {source} snapshot for {status.event_key}; "
                          f"this pit is at {self._event_key}.")
            return False
        held = self._status
        if held is not None and status.data_as_of <= held.data_as_of:
            return False
        self._status = status
        self.status_changed.emit(status)
        if held is None or held.now_queuing != status.now_queuing:
            self.now_queuing_changed.emit(status.now_queuing)
        if held is None or held.announcements != status.announcements:
            self.announcements_changed.emit(list(status.announcements))
        if held is None or held.parts_requests != status.parts_requests:
            self.parts_requests_changed.emit(list(status.parts_requests))
        self._emit_match_if_changed()
        self._set_state("live", self._live_message(source))
        return True

    # ── The relay socket ─────────────────────────────────────────────────

    def _on_relay_status(self, status: EventStatus) -> None:
        if self._offer_status(status, "relay push"):
            self._status_source = "relay push"

    def _on_relay_stats(self, _stats: dict) -> None:
        self.relay_changed.emit()

    def _on_relay_connection(self, up: bool) -> None:
        if up:
            self._fallback_timer.stop()
            if self._live_timer.isActive():
                self._live_timer.stop()
                self.log.emit("Relay connected — live updates are pushed; "
                              "direct polling stopped.")
            else:
                self.log.emit("Relay connected — live updates are pushed.")
        elif self.configured and not self._quiet:
            secs = int(settings.get("fallback_after_s"))
            self._fallback_timer.start(secs * 1000)
            self.log.emit(f"Relay link dropped — reconnecting; polling "
                          f"takes over in {secs}s if it stays down.")
        self.relay_changed.emit()

    def _on_fallback_due(self) -> None:
        if self._relay.connected or not self.configured:
            return
        if settings.get("auto_poll"):
            self.log.emit("Relay unreachable — polling for the live snapshot "
                          "until it is back.")
            self._start_live_timer()
            self.poll()
        else:
            self.log.emit("Relay unreachable and automatic polling is off — "
                          "the live snapshot will not update.")

    def _emit_match_if_changed(self) -> None:
        m = self.next_match() or self.current_match()
        key = None if m is None else (m.label, m.status, m.times)
        if key != self._last_match_key:
            self._last_match_key = key
            self.match_changed.emit(m)

    def _on_team_changed(self, _team) -> None:
        # Everything "ours" is derived from config at call time; only the
        # signal needs re-firing so boards re-read.
        self._emit_match_if_changed()
        if self._pits:
            self.pits_changed.emit(dict(self._pits))
        if self._inspection:
            self.inspection_changed.emit(dict(self._inspection))
        if self._alliances is not None:
            self.alliances_changed.emit(self._alliances)

    def _clear_event_data(self) -> None:
        had = self._status is not None
        self._status = None
        self._pits = {}
        self._pit_map = None
        self._inspection = {}
        self._teams = []
        self._alliances = None
        self._last_match_key = None
        if had:
            self.status_changed.emit(None)
            self.now_queuing_changed.emit(None)
            self.announcements_changed.emit([])
            self.parts_requests_changed.emit([])
        self.match_changed.emit(None)
        self.pits_changed.emit({})
        self.pit_map_changed.emit(None)
        self.inspection_changed.emit({})
        self.teams_changed.emit([])
        self.alliances_changed.emit(None)

    # ── The worker ───────────────────────────────────────────────────────

    def _enqueue(self, job: str, fn: Callable[[], Any]) -> None:
        if any(j == job for j, _ in self._queue):
            return
        if self._worker is not None and self._worker.objectName() == job:
            return
        self._queue.append((job, fn))
        self._pump()

    def _pump(self) -> None:
        if self.busy or not self._queue:
            return
        job, fn = self._queue.pop(0)
        worker = _Fetch(job, fn, parent=self)
        worker.setObjectName(job)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        self.busy_changed.emit(True)
        worker.start()

    def _on_done(self, job: str, result) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        door, result = result if isinstance(result, tuple) else ("", result)
        if door:
            self._source[job] = door
        if job == "relay":
            self.relay_changed.emit()
            return
        if job == "status":
            fresh = self._offer_status(result, f"poll via {door or 'nexus'}")
            if fresh:
                self._status_source = door
            settings.save(last_poll=stamp,
                          last_result="ok" if fresh else "ok (no newer data)")
            if not fresh and self._status is not None:
                # Same snapshot as held, but the status line's clock moves.
                self._set_state("live", self._live_message(self._status_source or "poll"))
        elif job == "pits":
            self._pits = result
            self.pits_changed.emit(dict(result))
        elif job == "map":
            self._pit_map = result
            self.pit_map_changed.emit(result)
        elif job == "inspection":
            self._inspection = result
            self.inspection_changed.emit(dict(result))
        elif job == "teams":
            self._teams = result
            self.teams_changed.emit(list(result))
        elif job == "alliances":
            self._alliances = result
            self.alliances_changed.emit(result)
        elif job == "events":
            self._events = result
            self.events_changed.emit(dict(result))

    _EMPTY = {"pits": {}, "map": None, "inspection": {}, "teams": [],
              "alliances": None}

    def _on_failed(self, job: str, message: str, code) -> None:
        if job in self._EMPTY and code == 404:
            # Not an error: the event has nothing of this kind (a demo event
            # has no pit map; alliances do not exist until Saturday). Hold
            # the empty value so a board asking gets an honest nothing.
            self.log.emit(message)
            self._on_done(job, self._EMPTY[job])
            return
        if job == "status":
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            settings.save(last_poll=stamp, last_result=f"failed: {message[:80]}")
            self._set_state("error", message)
            # The event itself is missing or the key is refused: every other
            # fetch against it would say the same thing.
            if code in (401, 403, 404):
                self._queue = [(j, f) for j, f in self._queue if j == "events"]
        else:
            self.log.emit(f"{job}: {message}")
            if self._status is None and self._state == "polling":
                self._set_state("error", message)

    def _on_finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self.busy_changed.emit(False)
        self._pump()

    # ── Timers and state ────────────────────────────────────────────────

    def _start_feed(self) -> None:
        """
        Bring the feed up for the current event: the relay socket if there is
        one, the slow timer always, the live timer only when nothing pushes.
        """
        prefs = settings.load()
        self._slow_timer.start(prefs["slow_poll_interval_s"] * 1000)
        if self.relay_configured:
            self._relay.start(prefs["relay_url"], self._event_key, api.relay_token())
            # If the socket never comes up, polling takes over after this.
            self._fallback_timer.start(int(prefs["fallback_after_s"]) * 1000)
        elif prefs["auto_poll"]:
            self._start_live_timer()

    def _start_live_timer(self) -> None:
        self._live_timer.start(int(settings.get("poll_interval_s")) * 1000)

    def _stop_timers(self) -> None:
        self._live_timer.stop()
        self._slow_timer.stop()
        self._fallback_timer.stop()

    @property
    def polling(self) -> bool:
        return self._live_timer.isActive()

    @property
    def pushed_live(self) -> bool:
        """The relay socket is up and delivering."""
        return self._relay.connected

    def _live_message(self, source: str) -> str:
        s = self._status
        if s is None:
            return ""
        parts = [f"{len(s.matches)} matches scheduled"]
        if s.now_queuing:
            parts.append(f"now queuing {s.now_queuing}")
        stamp = api.when(s.data_as_of)
        when_txt = stamp.strftime("%H:%M:%S") if stamp else "?"
        return f"Live · data as of {when_txt} by {source} · " + " · ".join(parts)

    def _sync_state(self) -> None:
        if not self.configured:
            self._set_state("off", self.blocked_reason)
        elif self._status is not None:
            self._set_state("live", self._live_message(self._status_source or "poll"))
        elif self._state not in ("polling", "error"):
            waiting = self.polling or self._relay.state != "off"
            self._set_state("idle", "Waiting for the first snapshot…" if waiting
                            else "Ready. Polling is off.")

    def _set_state(self, state: str, message: str) -> None:
        self._state = state
        self._message = message
        self.message_changed.emit(message)
        self.state_changed.emit(state)


nexus: _NexusService = LazyProxy("nexus", "init_nexus")  # type: ignore[assignment]


def init_nexus() -> _NexusService:
    """Call once in main(), after `init_config()` — it reads the active team."""
    real = _NexusService()
    nexus._install(real)
    return real
