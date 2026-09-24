"""
The Nexus API, v1.8.0 — `https://frc.nexus/api/v1`. No Qt, no I/O beyond HTTP.

Nexus is the queuing system most FRC events run: the lead queuer moves matches
through *Queuing soon → Now queuing → On deck → On field* on a tablet, and this
API is that tablet's state, plus the pit map, the inspection queue and the
alliance board. It is the only place the pit can learn "you are on deck" from
without somebody running back from the field.

Every endpoint in the published spec is here, one method each on `Client`, and
every field in every schema is carried on a model with the spec's own name and
meaning in its comment. `NEXUS.md` is the human copy of the same thing.

    Client.events()                 GET /events
    Client.event_status(key)        GET /event/{key}
    Client.pit_addresses(key)       GET /event/{key}/pits
    Client.pit_map(key)             GET /event/{key}/map
    Client.inspection(key)          GET /event/{key}/inspection
    Client.teams(key)               GET /event/{key}/teams
    Client.alliances(key)           GET /event/{key}/alliances

**Two ways in, one client.** `Client` talks to frc.nexus with the team's
`Nexus-Api-Key`. `RelayClient` talks to the team's relay
(`nexus-relay/`, `https://nexus.bh-stack.com`), which mirrors these exact
paths under its own `/api/v1/` and holds the Nexus key itself — so a pit
machine with only a relay token reaches every endpoint above. The two push
webhooks (`EventStatus`, `MatchStatus`) land on the relay, never here;
`classify_push()` is the shape rule the relay applies, kept here so
`--self-check` can hold the bundled examples to it.

Three facts about the data that shape everything downstream:

- **Every timestamp is Unix milliseconds**, and most are *estimates* that move
  every time a new snapshot arrives. `Match.times` keeps them as `int | None`
  in the unit the API uses; `when()` turns one into a local `datetime`.
- **Team numbers are strings.** `"3937"`, not `3937` — the spec says so and
  the alliance and pit maps key on them. `team_str()` is the one conversion.
- **Live timing is only real at events that queue with Nexus.** At any other
  event the schedule is still there but the estimates are meaningless. There
  is no flag for this; the docs say so in prose.

Attribution is a condition of use: anything that shows this data on a screen
links back to frc.nexus. `ATTRIBUTION` is the string to use.

`FakeClient` serves the spec's own example payloads (`assets/nexus/examples.json`)
and is what `PIT_NEXUS_FAKE=1` swaps in — every screen can be developed and
`--self-check`ed without a key or a network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from app import credentials, net, paths

BASE_URL = os.environ.get("PIT_NEXUS_API", "https://frc.nexus/api/v1")
API_KEY_SECRET = "nexus_api_key"
RELAY_TOKEN_SECRET = "nexus_relay_token"
ATTRIBUTION = "Event data from frc.nexus"
ATTRIBUTION_URL = "https://frc.nexus"

_TIMEOUT = 15
_USER_AGENT = "breakaway-pit-display"


class NexusError(Exception):
    """Anything that stops a fetch, phrased for an operator in a pit."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# ── Enumerations, exactly as the spec spells them ────────────────────────────

class MatchState:
    """
    `Match.status`. Typically a match walks down this list in order, but the
    spec is explicit that *any* transition is possible and some events skip
    `Now queuing` entirely — so never assume the previous state.
    """
    QUEUING_SOON = "Queuing soon"
    NOW_QUEUING = "Now queuing"
    ON_DECK = "On deck"
    ON_FIELD = "On field"
    ORDER = (QUEUING_SOON, NOW_QUEUING, ON_DECK, ON_FIELD)

    @classmethod
    def rank(cls, status: str) -> int:
        """Position in the usual progression; unknown strings sort first."""
        try:
            return cls.ORDER.index(status)
        except ValueError:
            return -1


class InspectionState:
    """`InspectionStatus.status`. Cached upstream and "may be a couple minutes
    out of date"; absent entirely for demo events."""
    HOLD = "hold"
    IN_PROGRESS = "in-progress"
    COMPLETE = "complete"
    REINSPECTION = "reinspection"
    QUEUED = "queued"
    NOT_STARTED = "not-started"
    ALL = (HOLD, IN_PROGRESS, COMPLETE, REINSPECTION, QUEUED, NOT_STARTED)


class BreakKind:
    """`Match.breakAfter` — the break that begins once this match is played."""
    BREAK = "Break"
    LUNCH = "Lunch"
    END_OF_DAY = "End of day"
    ALLIANCE_SELECTION = "Alliance selection"
    AWARDS = "Awards break"
    ALL = (BREAK, LUNCH, END_OF_DAY, ALLIANCE_SELECTION, AWARDS)


class ArrowType:
    SINGLE = "single"      # points up at 0°
    DOUBLE = "double"      # points up and down at 0°


class ArrowColor:
    RED = "red"
    BLUE = "blue"          # the default when the field is null
    PURPLE = "purple"
    GRAY = "gray"


# ── Models ───────────────────────────────────────────────────────────────────

def team_str(team: int | str | None) -> str | None:
    """Nexus keys everything on team numbers *as strings*."""
    if team is None:
        return None
    return str(team).strip()


def when(ms: int | None) -> datetime | None:
    """A Unix-millisecond timestamp as a local, aware datetime."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()


def _ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _teams(value: Any) -> list[str | None] | None:
    """`Teams`: an array in station order; a null entry is an empty station
    (practice), a null array is an undecided alliance (playoffs)."""
    if value is None:
        return None
    return [None if t is None else str(t) for t in value]


@dataclass(frozen=True)
class EventSummary:
    """`EventSummary` — one row of `GET /events`. Registered for Nexus, which
    does not promise the event uses any feature of it. Events drop off the
    list once they have concluded."""
    key: str
    name: str
    start: int          # Unix ms, scheduled start
    end: int            # Unix ms, scheduled end

    @property
    def is_live(self) -> bool:
        now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        return self.start <= now <= self.end


@dataclass(frozen=True)
class MatchTimes:
    """
    `Match.times`. All Unix ms, all nullable. The `estimated*` fields collapse
    onto their `actual*` twin once that stage is reached, so reading the
    estimate is always right and the actual says whether it has happened.
    """
    # Originally scheduled start. Not set for playoffs, or when there is no schedule.
    scheduled_start: int | None = None
    # Estimated `Now queuing`; equals `actual_queue` once past `Queuing soon`.
    estimated_queue: int | None = None
    # Estimated `On deck`; equals `actual_on_deck` once on deck or on field.
    estimated_on_deck: int | None = None
    # Estimated `On field`; equals `actual_on_field` once on field.
    estimated_on_field: int | None = None
    # Estimated "3-2-1-Go".
    estimated_start: int | None = None
    # When the status became `Now queuing`. Null while `Queuing soon`.
    actual_queue: int | None = None
    # When the status became `On deck`. Null while `Queuing soon` / `Now queuing`.
    actual_on_deck: int | None = None
    # When the status became `On field`. Null unless `On field`.
    actual_on_field: int | None = None
    # When the match actually started. Null until then, and always null at an
    # event not using Nexus AutoQueue.
    actual_start: int | None = None
    # When scores were committed. Same AutoQueue caveat.
    actual_commit: int | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "MatchTimes":
        raw = raw or {}
        return cls(
            scheduled_start=_ms(raw.get("scheduledStartTime")),
            estimated_queue=_ms(raw.get("estimatedQueueTime")),
            estimated_on_deck=_ms(raw.get("estimatedOnDeckTime")),
            estimated_on_field=_ms(raw.get("estimatedOnFieldTime")),
            estimated_start=_ms(raw.get("estimatedStartTime")),
            actual_queue=_ms(raw.get("actualQueueTime")),
            actual_on_deck=_ms(raw.get("actualOnDeckTime")),
            actual_on_field=_ms(raw.get("actualOnFieldTime")),
            actual_start=_ms(raw.get("actualStartTime")),
            actual_commit=_ms(raw.get("actualCommitTime")),
        )

    @property
    def played(self) -> bool:
        """Scores are in. Only ever true at an AutoQueue event."""
        return self.actual_commit is not None


@dataclass(frozen=True)
class Match:
    """`Match` — one scheduled match, or a scheduled replay of one."""
    label: str                              # "Qualification 24", "Final 1", …
    status: str                             # a `MatchState` value
    red_teams: list[str | None] | None      # station order; up to 4 in playoffs
    blue_teams: list[str | None] | None
    times: MatchTimes
    break_after: str | None = None          # a `BreakKind` value, or None
    replay_of: str | None = None            # the label this is a replay of

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Match":
        return cls(
            label=str(raw.get("label", "")),
            status=str(raw.get("status", "")),
            red_teams=_teams(raw.get("redTeams")),
            blue_teams=_teams(raw.get("blueTeams")),
            times=MatchTimes.from_json(raw.get("times")),
            break_after=raw.get("breakAfter"),
            replay_of=raw.get("replayOf"),
        )

    def alliance_of(self, team: int | str) -> str | None:
        """`"red"`, `"blue"`, or None if the team is not in this match."""
        t = team_str(team)
        if self.red_teams and t in self.red_teams:
            return "red"
        if self.blue_teams and t in self.blue_teams:
            return "blue"
        return None

    def has_team(self, team: int | str) -> bool:
        return self.alliance_of(team) is not None

    @property
    def is_replay(self) -> bool:
        return bool(self.replay_of)

    @property
    def on_field(self) -> bool:
        return self.status == MatchState.ON_FIELD

    @property
    def level(self) -> str:
        """`practice` / `qualification` / `playoff` / `final`, read off the label."""
        head = self.label.split(" ", 1)[0].lower()
        return {"practice": "practice", "qualification": "qualification",
                "playoff": "playoff", "final": "final"}.get(head, "other")


@dataclass(frozen=True)
class Announcement:
    id: str
    text: str
    posted: int         # Unix ms

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Announcement":
        return cls(id=str(raw.get("id", "")),
                   text=str(raw.get("announcement", "")),
                   posted=_ms(raw.get("postedTime")) or 0)


@dataclass(frozen=True)
class PartsRequest:
    id: str
    parts: str
    requested_by: str   # team number, as a string
    posted: int         # Unix ms

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "PartsRequest":
        return cls(id=str(raw.get("id", "")),
                   parts=str(raw.get("parts", "")),
                   requested_by=str(raw.get("requestedByTeam", "")),
                   posted=_ms(raw.get("postedTime")) or 0)


@dataclass(frozen=True)
class EventStatus:
    """
    `EventStatus` — the whole event in one snapshot. The pull endpoint and the
    live-event webhook both deliver this. `data_as_of` is the tie-breaker when
    two arrive close together: the spec says always keep the newer.
    """
    event_key: str
    data_as_of: int                         # Unix ms, when Nexus built the snapshot
    now_queuing: str | None                 # latest match label queuing, or None
    matches: list[Match]                    # play order: practice, quals, playoffs
    announcements: list[Announcement]
    parts_requests: list[PartsRequest]

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "EventStatus":
        return cls(
            event_key=str(raw.get("eventKey", "")),
            data_as_of=_ms(raw.get("dataAsOfTime")) or 0,
            now_queuing=raw.get("nowQueuing"),
            matches=[Match.from_json(m) for m in raw.get("matches") or []],
            announcements=[Announcement.from_json(a)
                           for a in raw.get("announcements") or []],
            parts_requests=[PartsRequest.from_json(p)
                            for p in raw.get("partsRequests") or []],
        )

    # ── Derived views ────────────────────────────────────────────────────

    def matches_for(self, team: int | str) -> list[Match]:
        return [m for m in self.matches if m.has_team(team)]

    def match(self, label: str) -> Match | None:
        for m in self.matches:
            if m.label == label:
                return m
        return None

    def upcoming_for(self, team: int | str) -> list[Match]:
        """The team's matches that are not yet `On field`, in play order."""
        return [m for m in self.matches_for(team) if not m.on_field]

    def next_for(self, team: int | str) -> Match | None:
        """
        The match the team should be thinking about: the first of theirs that
        is not yet on the field. On a fresh snapshot a match that has *just*
        gone on field is still the live one; `current_for` covers that.
        """
        up = self.upcoming_for(team)
        return up[0] if up else None

    def current_for(self, team: int | str) -> Match | None:
        """The team's match that is on the field right now, if any — the last
        `On field` entry of theirs, since Nexus never marks a match finished."""
        played = [m for m in self.matches_for(team) if m.on_field]
        return played[-1] if played else None

    def queuing_match(self) -> Match | None:
        return self.match(self.now_queuing) if self.now_queuing else None

    def by_status(self, status: str) -> list[Match]:
        return [m for m in self.matches if m.status == status]

    def next_break(self) -> tuple[Match, str] | None:
        """The first break still ahead: (the match it follows, its kind)."""
        for m in self.matches:
            if m.break_after and not m.on_field:
                return m, m.break_after
        return None


@dataclass(frozen=True)
class MatchStatus:
    """`MatchStatus` — the team-specific webhook body: one match, updated."""
    event_key: str
    data_as_of: int
    match: Match

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "MatchStatus":
        return cls(event_key=str(raw.get("eventKey", "")),
                   data_as_of=_ms(raw.get("dataAsOfTime")) or 0,
                   match=Match.from_json(raw.get("match") or {}))


@dataclass(frozen=True)
class InspectionStatus:
    """One team's row of `GET /event/{key}/inspection`."""
    team: str
    inspected: bool                 # passed an initial, complete inspection
    status: str | None              # an `InspectionState` value; None at demo events
    queue_position: int | None      # place in the inspection queue, if queued

    @classmethod
    def from_json(cls, team: str, raw: dict[str, Any]) -> "InspectionStatus":
        pos = raw.get("queuePosition")
        return cls(team=str(team),
                   inspected=bool(raw.get("inspected", False)),
                   status=raw.get("status"),
                   queue_position=int(pos) if pos is not None else None)


@dataclass(frozen=True)
class MapElement:
    """
    `MapElement` — the geometry every pit-map object shares. Units are map
    units, **10 to the foot**; `position` is the element's *centre* and
    `angle` its rotation about that centre, in degrees.
    """
    x: float
    y: float
    width: float
    height: float
    angle: float = 0.0

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "MapElement":
        pos = raw.get("position") or {}
        size = raw.get("size") or {}
        return cls(x=float(pos.get("x", 0)), y=float(pos.get("y", 0)),
                   width=float(size.get("x", 0)), height=float(size.get("y", 0)),
                   angle=float(raw.get("angle") or 0.0))


@dataclass(frozen=True)
class Pit:
    address: str            # "A1"
    team: str | None        # assigned team, or None if empty
    shape: MapElement


@dataclass(frozen=True)
class Area:
    id: str
    label: str              # "Pit admin", "Inspection", "Spare parts", "EMT", …
    shape: MapElement


@dataclass(frozen=True)
class MapLabel:
    id: str
    label: str              # "Field", "Practice field", "Main gym", …
    shape: MapElement


@dataclass(frozen=True)
class Arrow:
    id: str
    type: str               # `ArrowType`
    color: str              # `ArrowColor`; the API's null is `blue`
    shape: MapElement


@dataclass(frozen=True)
class Wall:
    id: str
    shape: MapElement


@dataclass(frozen=True)
class PitMap:
    """`PitMap` — `GET /event/{key}/map`. `width`/`height` are the canvas."""
    width: float
    height: float
    pits: dict[str, Pit]            # keyed by pit address
    areas: dict[str, Area]
    labels: dict[str, MapLabel]
    arrows: dict[str, Arrow]
    walls: dict[str, Wall]

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "PitMap":
        size = raw.get("size") or {}
        pits = {k: Pit(address=str(k), team=(None if v.get("team") is None
                                              else str(v["team"])),
                       shape=MapElement.from_json(v))
                for k, v in (raw.get("pits") or {}).items()}
        areas = {k: Area(id=str(k), label=str(v.get("label", "")),
                         shape=MapElement.from_json(v))
                 for k, v in (raw.get("areas") or {}).items()}
        labels = {k: MapLabel(id=str(k), label=str(v.get("label", "")),
                              shape=MapElement.from_json(v))
                  for k, v in (raw.get("labels") or {}).items()}
        arrows = {k: Arrow(id=str(k), type=str(v.get("type", ArrowType.SINGLE)),
                           color=str(v.get("color") or ArrowColor.BLUE),
                           shape=MapElement.from_json(v))
                  for k, v in (raw.get("arrows") or {}).items()}
        walls = {k: Wall(id=str(k), shape=MapElement.from_json(v))
                 for k, v in (raw.get("walls") or {}).items()}
        return cls(width=float(size.get("x", 0)), height=float(size.get("y", 0)),
                   pits=pits, areas=areas, labels=labels, arrows=arrows,
                   walls=walls)

    def pit_of(self, team: int | str) -> Pit | None:
        t = team_str(team)
        for pit in self.pits.values():
            if pit.team == t:
                return pit
        return None

    def area_named(self, label: str) -> Area | None:
        needle = label.strip().lower()
        for area in self.areas.values():
            if area.label.strip().lower() == needle:
                return area
        return None


@dataclass(frozen=True)
class Alliance:
    """
    One playoff alliance: `[captain, first pick, second pick]`, and a fourth
    slot when a backup was called. Any slot is None while selection is still
    in progress; a whole alliance can be None too.
    """
    number: int                     # 1-based seed
    teams: list[str | None]

    @property
    def captain(self) -> str | None:
        return self.teams[0] if self.teams else None

    @property
    def picks(self) -> list[str | None]:
        return self.teams[1:]

    @property
    def complete(self) -> bool:
        return len(self.teams) >= 3 and all(self.teams[:3])

    def has_team(self, team: int | str) -> bool:
        return team_str(team) in self.teams


@dataclass(frozen=True)
class Alliances:
    alliances: list[Alliance | None]

    @classmethod
    def from_json(cls, raw: list) -> "Alliances":
        out: list[Alliance | None] = []
        for i, entry in enumerate(raw or []):
            if entry is None:
                out.append(None)
            else:
                out.append(Alliance(number=i + 1,
                                    teams=[None if t is None else str(t)
                                           for t in entry]))
        return cls(alliances=out)

    def alliance_of(self, team: int | str) -> Alliance | None:
        for a in self.alliances:
            if a is not None and a.has_team(team):
                return a
        return None

    @property
    def selection_started(self) -> bool:
        return any(a is not None and a.captain for a in self.alliances)

    @property
    def selection_complete(self) -> bool:
        return bool(self.alliances) and all(a is not None and a.complete
                                            for a in self.alliances)


# ── Parsing the two webhook bodies ───────────────────────────────────────────

def parse_event_status(raw: dict[str, Any]) -> EventStatus:
    return EventStatus.from_json(raw)


def parse_match_status(raw: dict[str, Any]) -> MatchStatus:
    return MatchStatus.from_json(raw)


def classify_push(raw: Any) -> str | None:
    """
    Which webhook a body is: `"event"`, `"match"`, or None if neither.

    Both webhooks arrive at whatever URL the team registered, and nothing in
    the headers says which one it is — the shape does. `MatchStatus` has a
    single `match`; `EventStatus` has `matches`.
    """
    if not isinstance(raw, dict) or "eventKey" not in raw:
        return None
    if isinstance(raw.get("match"), dict):
        return "match"
    if isinstance(raw.get("matches"), list):
        return "event"
    return None


# ── The client ───────────────────────────────────────────────────────────────

def api_key() -> str:
    return credentials.read(API_KEY_SECRET)


def configured() -> bool:
    return bool(api_key())


def set_api_key(value: str) -> None:
    credentials.write(API_KEY_SECRET, value)


def relay_token() -> str:
    return credentials.read(RELAY_TOKEN_SECRET)


def set_relay_token(value: str) -> None:
    credentials.write(RELAY_TOKEN_SECRET, value)


def fake_enabled() -> bool:
    return os.environ.get("PIT_NEXUS_FAKE", "") not in ("", "0")


class Client:
    """
    One method per endpoint. Every method raises `NexusError` on anything but
    a 200 with a JSON body, with the message the spec gives for that code.
    """

    # How this client names where it is fetching from, in every error.
    WHERE = "frc.nexus"

    def __init__(self, key: str | None = None, base_url: str = BASE_URL):
        self._key = key
        self._base = base_url.rstrip("/")

    @property
    def key(self) -> str:
        return self._key if self._key is not None else api_key()

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _auth(self) -> dict[str, str]:
        key = self.key
        if not key:
            raise NexusError(
                "No Nexus API key on this machine. Get one at frc.nexus/api "
                "and paste it into Control → Event Feed, or put it in "
                "secrets/nexus_api_key.", 401)
        return {"Nexus-Api-Key": key}

    def _get(self, path: str) -> Any:
        url = f"{self._base}{path}"
        req = urllib.request.Request(url, headers={
            **self._auth(),
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                        context=net.ssl_context()) as r:
                body = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise NexusError(self._explain(e.code, path), e.code) from e
        except urllib.error.URLError as e:
            host = urllib.parse.urlparse(url).netloc
            raise NexusError(net.describe_url_error(e, host, self.WHERE)) from e
        except OSError as e:
            raise NexusError(f"Could not reach {self.WHERE}: {e}") from e
        try:
            return json.loads(body)
        except ValueError as e:
            raise NexusError(f"{self.WHERE} returned something that is not JSON.") from e

    @staticmethod
    def _explain(code: int, path: str) -> str:
        # The spec's own wording for each status, minus the hyperlinks.
        if code == 401:
            return ("Missing API key — frc.nexus did not see a Nexus-Api-Key "
                    "header. Get a key at frc.nexus/api.")
        if code == 403:
            return ("frc.nexus refused the API key. Check it matches the key at "
                    "frc.nexus/api; a key that was abused may have been "
                    "disabled (contact@frc.nexus).")
        if code == 404:
            if path == "/events":
                return "frc.nexus lists no active events right now."
            parts = path.strip("/").split("/")
            key = parts[1] if len(parts) >= 2 else "?"
            # Only the event itself 404ing means the key is wrong. A
            # sub-resource 404s when the event has nothing of that kind —
            # a demo event has no pit map, a Thursday has no alliances.
            if len(parts) >= 3:
                what = {"pits": "pit addresses", "map": "pit map",
                        "inspection": "inspection data",
                        "teams": "team list",
                        "alliances": "alliances"}.get(parts[2], parts[2])
                return f"No {what} published for {key} yet."
            return (f"Event “{key}” does not exist on frc.nexus. Event keys "
                    "look like 2024casf; demo keys like demo1234.")
        if code == 500:
            return ("frc.nexus had a server error. Try again in a minute; if "
                    "it keeps happening, email contact@frc.nexus.")
        return f"frc.nexus returned HTTP {code}."

    # ── Endpoints ────────────────────────────────────────────────────────

    def events(self) -> dict[str, EventSummary]:
        """`GET /events` — every event currently registered for Nexus, by key."""
        raw = self._get("/events")
        if not isinstance(raw, dict):
            raise NexusError("frc.nexus returned something that is not an event map.")
        return {k: EventSummary(key=str(k), name=str(v.get("name", "")),
                                start=_ms(v.get("start")) or 0,
                                end=_ms(v.get("end")) or 0)
                for k, v in raw.items()}

    def event_status(self, event_key: str) -> EventStatus:
        """`GET /event/{eventKey}` — the live snapshot."""
        return parse_event_status(self._get(f"/event/{_seg(event_key)}"))

    def pit_addresses(self, event_key: str) -> dict[str, str]:
        """`GET /event/{eventKey}/pits` — team number → pit address."""
        raw = self._get(f"/event/{_seg(event_key)}/pits")
        if not isinstance(raw, dict):
            raise NexusError("frc.nexus returned something that is not a pit map.")
        return {str(k): str(v) for k, v in raw.items()}

    def pit_map(self, event_key: str) -> PitMap:
        """`GET /event/{eventKey}/map` — the drawn pit layout."""
        return PitMap.from_json(self._get(f"/event/{_seg(event_key)}/map"))

    def inspection(self, event_key: str) -> dict[str, InspectionStatus]:
        """`GET /event/{eventKey}/inspection` — team number → status."""
        raw = self._get(f"/event/{_seg(event_key)}/inspection")
        if not isinstance(raw, dict):
            raise NexusError("frc.nexus returned something that is not an inspection map.")
        return {str(k): InspectionStatus.from_json(str(k), v or {})
                for k, v in raw.items()}

    def teams(self, event_key: str) -> list[str]:
        """`GET /event/{eventKey}/teams` — team numbers attending."""
        raw = self._get(f"/event/{_seg(event_key)}/teams")
        if not isinstance(raw, list):
            raise NexusError("frc.nexus returned something that is not a team list.")
        return [str(t) for t in raw]

    def alliances(self, event_key: str) -> Alliances:
        """`GET /event/{eventKey}/alliances` — the playoff alliance board."""
        raw = self._get(f"/event/{_seg(event_key)}/alliances")
        if not isinstance(raw, list):
            raise NexusError("frc.nexus returned something that is not an alliance list.")
        return Alliances.from_json(raw)

    def everything(self, event_key: str) -> "EventBundle":
        """All six per-event endpoints in one call, for a manual refresh."""
        return EventBundle(
            status=self.event_status(event_key),
            pits=self.pit_addresses(event_key),
            pit_map=self.pit_map(event_key),
            inspection=self.inspection(event_key),
            teams=self.teams(event_key),
            alliances=self.alliances(event_key),
        )


def _seg(value: str) -> str:
    return urllib.parse.quote(str(value).strip(), safe="")


class RelayClient(Client):
    """
    The same endpoints, through the team's relay.

    The relay mirrors Nexus's paths under `{relay}/api/v1`, answers with the
    same bodies and passes Nexus's own 404s through, so every method above
    works unchanged — only the header and the error wording differ. It adds
    one path of its own, `relay_stats()`, which is how the pit sees whether
    webhooks are reaching the relay at all.
    """

    WHERE = "the Nexus relay"

    def __init__(self, relay_url: str, token: str | None = None):
        super().__init__(key="", base_url=relay_url.rstrip("/") + "/api/v1")
        self._token = token

    @property
    def token(self) -> str:
        return self._token if self._token is not None else relay_token()

    def _auth(self) -> dict[str, str]:
        token = self.token
        if not token:
            raise NexusError("No relay token on this machine. Paste it into "
                             "Control → Event Feed (admin), or put it in "
                             "secrets/nexus_relay_token.", 401)
        return {"Authorization": f"Bearer {token}"}

    def _explain(self, code: int, path: str) -> str:
        if code == 401:
            return ("The relay refused this machine's token. It must match the "
                    "relay's CLIENT_TOKEN exactly.")
        if code == 502:
            return "The relay could not reach frc.nexus. Nexus may be down."
        if code == 503:
            return ("The relay has no Nexus API key of its own — run "
                    "`npx wrangler secret put NEXUS_API_KEY` in nexus-relay/.")
        return super()._explain(code, path)

    def relay_stats(self, event_key: str) -> dict[str, Any]:
        """`GET /event/{key}/relay` — the relay's own counters for this event."""
        raw = self._get(f"/event/{_seg(event_key)}/relay")
        if not isinstance(raw, dict):
            raise NexusError("The relay returned something that is not a stats object.")
        return raw


@dataclass
class EventBundle:
    status: EventStatus
    pits: dict[str, str]
    pit_map: PitMap
    inspection: dict[str, InspectionStatus]
    teams: list[str]
    alliances: Alliances


# ── The fake ─────────────────────────────────────────────────────────────────

FIXTURES = ("assets", "nexus", "examples.json")


def load_fixtures() -> dict[str, Any]:
    with open(paths.resource(*FIXTURES), encoding="utf-8") as f:
        return json.load(f)


class FakeClient(Client):
    """
    The spec's example payloads, served in order.

    Each `event_status()` call steps through the eight example snapshots —
    empty schedule, pre-practice, mid-practice, … mid-playoffs — so a screen
    polling it sees an event actually progress. Every other endpoint returns
    the example for it. `event_key` is ignored except that an empty one 404s
    the way the real API would.
    """

    def __init__(self, fixtures: dict[str, Any] | None = None):
        super().__init__(key="fake")
        self._fx = fixtures if fixtures is not None else load_fixtures()
        self._step = 0

    def _get(self, path: str) -> Any:              # never called
        raise NexusError(f"FakeClient has no route for {path}", 404)

    def _need(self, event_key: str) -> None:
        if not str(event_key).strip():
            raise NexusError(self._explain(404, f"/event/{event_key}"), 404)

    def events(self) -> dict[str, EventSummary]:
        return {k: EventSummary(key=str(k), name=str(v["name"]),
                                start=int(v["start"]), end=int(v["end"]))
                for k, v in self._fx["events"].items()}

    def event_status(self, event_key: str) -> EventStatus:
        self._need(event_key)
        snaps = self._fx["event_status"]
        raw = dict(snaps[self._step % len(snaps)])
        self._step += 1
        raw["eventKey"] = event_key
        # The examples are frozen in May 2024; stamp the snapshot as now so a
        # panel showing "data as of" does not claim to be two years stale.
        raw["dataAsOfTime"] = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        return parse_event_status(raw)

    def pit_addresses(self, event_key: str) -> dict[str, str]:
        self._need(event_key)
        return {str(k): str(v) for k, v in self._fx["pits"].items()}

    def pit_map(self, event_key: str) -> PitMap:
        self._need(event_key)
        return PitMap.from_json(self._fx["map_simple"])

    def inspection(self, event_key: str) -> dict[str, InspectionStatus]:
        self._need(event_key)
        return {str(k): InspectionStatus.from_json(str(k), v)
                for k, v in self._fx["inspection"].items()}

    def teams(self, event_key: str) -> list[str]:
        self._need(event_key)
        return [str(t) for t in self._fx["teams"]]

    def alliances(self, event_key: str) -> Alliances:
        self._need(event_key)
        return Alliances.from_json(self._fx["alliances"])

    def match_pushes(self) -> Iterable[MatchStatus]:
        """The four example match-status webhook bodies, parsed."""
        return [parse_match_status(m) for m in self._fx["match_status"]]


def make_client() -> Client:
    """The real client, or the fake when `PIT_NEXUS_FAKE=1`."""
    return FakeClient() if fake_enabled() else Client()
