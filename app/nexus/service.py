"""
`_NexusService` — the singleton every screen reads the event through.

It owns one event key, one live `EventStatus` snapshot and the five slower
per-event fetches (pits, map, inspection, teams, alliances), polls them on two
cadences, accepts the push webhooks as an accelerator, and turns all of that
into the Qt signals the rest of the app is built on. Nothing else in the app
talks HTTP to Nexus.

    ┌ poll timer (30s) ──▶ GET /event/{key} ──────┐
    │                                             ▼
    │ webhook (push) ──▶ EventStatus / MatchStatus ─▶ newer dataAsOfTime? ─▶ snapshot
    │                                                                          │
    └ slow timer (5m) ──▶ pits · map · inspection · teams · alliances          │
                                                                               ▼
                       status_changed · match_changed · now_queuing_changed · …

**Newest `dataAsOfTime` wins, whichever way it arrived.** The spec says pushes
repeat and arrive out of order, and a poll can land between two pushes; every
snapshot goes through `_offer_status()`, which drops anything not newer than
what is held. A match push is folded into the held snapshot the same way —
one match replaced, the snapshot's clock moved to the push's.

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
from app.nexus.webhook import WebhookServer

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
    pushed = pyqtSignal(str, object)            # raw webhook relay
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

        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self.poll)
        self._slow_timer = QTimer(self)
        self._slow_timer.timeout.connect(self.poll_slow)

        self._webhook = WebhookServer(self)
        self._webhook.received.connect(self._on_push)
        self._webhook.rejected.connect(self.log.emit)

        config.team_changed.connect(self._on_team_changed)

        prefs = settings.load()
        self._event_key: str = prefs["event_key"]
        # `PIT_NEXUS_QUIET=1` builds the service without touching a timer or a
        # socket — what --self-check wants, since it runs beside the live app
        # on the same machine and must not take its webhook port or its poll.
        quiet = os.environ.get("PIT_NEXUS_QUIET", "") not in ("", "0")
        if not quiet and self.configured:
            if prefs["auto_poll"]:
                self._start_timers()
            QTimer.singleShot(_FIRST_POLL_MS, self.refresh)
        self._sync_state()
        if not quiet and prefs["webhook_enabled"]:
            self._start_webhook()

    # ── What is configured ───────────────────────────────────────────────

    @property
    def fake(self) -> bool:
        return isinstance(self._client, api.FakeClient)

    @property
    def event_key(self) -> str:
        return self._event_key

    @property
    def has_key(self) -> bool:
        return self.fake or api.configured()

    @property
    def configured(self) -> bool:
        return bool(self._event_key) and self.has_key

    @property
    def blocked_reason(self) -> str:
        if not self.has_key:
            return ("No Nexus API key on this machine. Get one at frc.nexus/api "
                    "and paste it below — it is stored in the secret folder, "
                    "never in the database.")
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
    def webhook(self) -> WebhookServer:
        return self._webhook

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
        if self.configured and settings.get("auto_poll"):
            self._start_timers()
        else:
            self._stop_timers()
        self._sync_state()
        if self.configured:
            self.refresh()

    def set_api_key(self, value: str) -> None:
        api.set_api_key(value)
        if self.configured and settings.get("auto_poll"):
            self._start_timers()
        else:
            self._stop_timers()
        self._sync_state()
        if self.configured:
            self.refresh()

    def set_auto_poll(self, on: bool) -> None:
        settings.save(auto_poll=bool(on))
        if on and self.configured:
            self._start_timers()
            self.poll()
        else:
            self._stop_timers()
        self._sync_state()

    def set_poll_interval(self, seconds: int) -> None:
        prefs = settings.save(poll_interval_s=int(seconds))
        if self._live_timer.isActive():
            self._live_timer.start(prefs["poll_interval_s"] * 1000)

    def set_webhook_enabled(self, on: bool) -> None:
        settings.save(webhook_enabled=bool(on))
        if on:
            self._start_webhook()
        else:
            self._webhook.stop()

    def set_webhook_port(self, port: int) -> int:
        prefs = settings.save(webhook_port=int(port))
        if self._webhook.listening:
            self._webhook.stop()
            self._start_webhook()
        return prefs["webhook_port"]

    def set_webhook_token(self, value: str) -> None:
        api.set_webhook_token(value)

    # ── Actions ──────────────────────────────────────────────────────────

    def poll(self) -> None:
        """One live snapshot. Silently a no-op when not configured."""
        if not self.configured:
            return
        key = self._event_key
        self._enqueue("status", lambda: self._client.event_status(key))

    def poll_slow(self) -> None:
        """The five slower per-event endpoints."""
        if not self.configured:
            return
        key = self._event_key
        c = self._client
        self._enqueue("pits", lambda: c.pit_addresses(key))
        self._enqueue("map", lambda: c.pit_map(key))
        self._enqueue("inspection", lambda: c.inspection(key))
        self._enqueue("teams", lambda: c.teams(key))
        self._enqueue("alliances", lambda: c.alliances(key))

    def refresh(self) -> None:
        """Everything, now — what the panel's Refresh button does."""
        if not self.configured:
            self._sync_state()
            return
        if self._status is None:
            self._set_state("polling", "Fetching the event from frc.nexus…")
        self.poll()
        self.poll_slow()

    def refresh_events(self) -> None:
        """`GET /events` — the picker's list. Needs a key, not an event."""
        if not self.has_key:
            self._set_state("off", self.blocked_reason)
            return
        self._enqueue("events", self._client.events)

    def shutdown(self) -> None:
        self._stop_timers()
        self._queue.clear()
        self._webhook.stop()
        if self._worker is not None:
            self._worker.wait(3_000)

    # ── Applying data ────────────────────────────────────────────────────

    def _offer_status(self, status: EventStatus, source: str) -> bool:
        """Adopt `status` if it is newer than what is held. Returns whether it was."""
        if status.event_key and self._event_key and status.event_key != self._event_key:
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

    def _on_push(self, kind: str, payload) -> None:
        self.pushed.emit(kind, payload)
        if kind == "event":
            if self._offer_status(payload, "push"):
                self.log.emit("Live event status received by webhook.")
            return
        ms: MatchStatus = payload
        if ms.event_key and self._event_key and ms.event_key != self._event_key:
            self.log.emit(f"Ignored a match push for {ms.event_key}.")
            return
        held = self._status
        if held is None:
            # Nothing to fold it into; a poll will bring the whole schedule.
            self.log.emit(f"Match push for {ms.match.label} before any "
                          "snapshot — polling.")
            self.poll()
            return
        if ms.data_as_of <= held.data_as_of:
            return
        matches = [ms.match if m.label == ms.match.label else m
                   for m in held.matches]
        if all(m.label != ms.match.label for m in held.matches):
            matches.append(ms.match)
        merged = EventStatus(event_key=held.event_key, data_as_of=ms.data_as_of,
                             now_queuing=held.now_queuing, matches=matches,
                             announcements=held.announcements,
                             parts_requests=held.parts_requests)
        if self._offer_status(merged, "push"):
            self.log.emit(f"{ms.match.label}: {ms.match.status} (webhook).")

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
        if job == "status":
            fresh = self._offer_status(result, "poll")
            settings.save(last_poll=stamp,
                          last_result="ok" if fresh else "ok (no newer data)")
            if not fresh and self._status is not None:
                # Same snapshot as held, but the status line's clock moves.
                self._set_state("live", self._live_message("poll"))
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

    # ── Timers, webhook, state ───────────────────────────────────────────

    def _start_timers(self) -> None:
        prefs = settings.load()
        self._live_timer.start(prefs["poll_interval_s"] * 1000)
        self._slow_timer.start(prefs["slow_poll_interval_s"] * 1000)

    def _stop_timers(self) -> None:
        self._live_timer.stop()
        self._slow_timer.stop()

    @property
    def polling(self) -> bool:
        return self._live_timer.isActive()

    def _start_webhook(self) -> None:
        port = int(settings.get("webhook_port"))
        problem = self._webhook.start(port)
        if problem:
            self.log.emit(problem)
        else:
            self.log.emit(f"Webhook listening on port {port}.")

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
            self._set_state("live", self._live_message("poll"))
        elif self._state not in ("polling", "error"):
            self._set_state("idle", "Ready. Polling is off." if not self.polling
                            else "Waiting for the first snapshot…")

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
