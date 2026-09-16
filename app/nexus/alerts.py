"""
`_AlertService` — turns the event feed into things the pit can see from
across the room: the strips, and a banner along the bottom of both overhead
screens.

Three alerts, and what each one means:

| alert | fires when | strips | banner |
|---|---|---|---|
| **First queue** | our next match goes `Now queuing` | sides flash the alliance colour 2 s at 1.5 Hz, steady 1 s, then back; centre run white throughout | alliance colour: `FIRST QUEUE · Qualification 24 · Red 2`, 10 s |
| **Second queue** | our next match goes `On deck` | same again | `SECOND QUEUE · …`, 10 s |
| **Inspection passed** | our inspection status turns to passed | sides flash green 5 s, centre white; then back | green: `INSPECTION PASSED`, 10 s |

"First queue" and "second queue" are the two calls a pit crew actually hears:
the first means *robot on the cart, to the queue line*; the second means
*the match ahead is on the field, get to the door*. Nexus's `Now queuing`
and `On deck` are those two calls. Some events skip `Now queuing` entirely,
so a match arriving straight at `On deck` fires the second call without
having fired the first — the alert is about the state, not the transition.

**Both are brief.** Three seconds of light is the attention-getter; the
banner is the statement — which call, which match, which station — and it
stays on both overhead screens for ten seconds, then the screens go back to
what they were showing. A pit's strips and screens are ambient, and a colour
that stays on for ten minutes stops being read. The Event Feed panel keeps
the standing fact (next match, status, station) for anyone who missed it.

**Every alert is sides-only, with the centre run white.** That is the whole
point of the per-segment white in fw 2.2: the sides carry the colour, the
centre stays a work light, whichever colour is being flashed.

**Inspection alerts on a transition, never on the first read.** The service
takes the first inspection status it sees as the baseline; a pit whose robot
passed yesterday must not celebrate on every launch.

Everything here is derived from `nexus` signals and `config.active_team`,
and every decision is re-taken when the team changes. The operator can fire
each alert by hand from Control → Event Feed → Alerts to check the strips and
the banner positions, and clear whatever is up.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app import brand
from app.config import config
from app.lazy_proxy import LazyProxy
from app.leds import leds
from app.leds.service import Alert
from app.nexus import nexus
from app.nexus.api import InspectionState, Match, MatchState, MatchTimes

# The strips: flash, then sit lit, then back to the resting look.
QUEUE_FLASH_S = 2.0
QUEUE_STEADY_S = 1.0
INSPECTION_FLASH_S = 5.0
INSPECTION_STEADY_S = 0.0
# The banner, whichever alert put it up.
BANNER_S = 10.0

FIRST_QUEUE = "first_queue"
SECOND_QUEUE = "second_queue"
INSPECTION_PASSED = "inspection_passed"

_STAGE = {MatchState.NOW_QUEUING: FIRST_QUEUE, MatchState.ON_DECK: SECOND_QUEUE}

# The banner wears the brand's hues; the strips get the saturated primaries.
# `palette.snap` would take Field Green to spring green and Sky to azure —
# both read as "not quite" across a pit, and an alliance colour has to be
# unmistakable. Pure hues snap to themselves.
ALLIANCE_COLOR = {"red": brand.RED, "blue": brand.SKY}
STRIP_COLOR = {"red": "#FF0000", "blue": "#0000FF"}
STRIP_GREEN = "#00FF00"


@dataclass(frozen=True)
class Banner:
    """What the overhead screens draw along the bottom while an alert is up."""
    kind: str                # FIRST_QUEUE | SECOND_QUEUE | INSPECTION_PASSED
    headline: str            # "FIRST QUEUE"
    detail: str              # "Qualification 24  ·  Red 2"
    color: str               # fill hex; type is always white on it
    alliance: str | None = None
    match_label: str = ""


def station_of(match: Match, team: str) -> tuple[str, int] | None:
    """`("red", 2)` — the alliance and 1-based driver station of `team`."""
    for side, teams in (("red", match.red_teams), ("blue", match.blue_teams)):
        if teams and team in teams:
            return side, teams.index(team) + 1
    return None


def queue_banner(kind: str, match: Match, team: str) -> Banner:
    placed = station_of(match, team)
    side, station = placed if placed else (None, 0)
    detail = match.label
    if side:
        detail += f"  ·  {side.capitalize()} {station}"
    return Banner(kind=kind,
                  headline="FIRST QUEUE" if kind == FIRST_QUEUE else "SECOND QUEUE",
                  detail=detail,
                  color=ALLIANCE_COLOR.get(side or "", brand.GRAPHITE),
                  alliance=side, match_label=match.label)


def inspection_banner() -> Banner:
    return Banner(kind=INSPECTION_PASSED, headline="INSPECTION PASSED",
                  detail="Robot is cleared to play", color=brand.FIELD_GREEN)


class _AlertService(QObject):

    # Banner | None — what both overhead screens should show along the bottom
    banner_changed = pyqtSignal(object)
    # One line for the Event Feed panel's log
    log = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._banner: Banner | None = None
        self._queue_key: tuple[str, str] | None = None   # (match label, stage)
        self._inspected: bool | None = None               # baseline, per team
        self._enabled = True
        self._banner_timer = QTimer(self)
        self._banner_timer.setSingleShot(True)
        self._banner_timer.timeout.connect(lambda: self._set_banner(None))

        nexus.match_changed.connect(self._on_match)
        nexus.inspection_changed.connect(self._on_inspection)
        nexus.event_key_changed.connect(lambda _k: self._reset())
        config.team_changed.connect(lambda _t: self._reset())

    # ── State ────────────────────────────────────────────────────────────

    @property
    def banner(self) -> Banner | None:
        return self._banner

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if not on:
            self.clear()

    # ── Feed reactions ───────────────────────────────────────────────────

    def _on_match(self, match) -> None:
        if not self._enabled:
            return
        stage = _STAGE.get(match.status) if match is not None else None
        if stage is None:
            # Queuing soon, on field, or no match at all: nothing is called.
            self._queue_key = None
            return
        key = (match.label, stage)
        if key == self._queue_key:
            return                                # a times-only change
        self._queue_key = key
        self._fire_queue(stage, match)

    def _on_inspection(self, statuses: dict) -> None:
        ours = statuses.get(nexus.our_team)
        if ours is None:
            return
        # `status` is the live word when the event sends one — so coming back
        # to `complete` from a re-inspection fires again. Demo events send
        # only `inspected`, which never un-sets once the initial pass is in.
        if ours.status is not None:
            passed = ours.status == InspectionState.COMPLETE
        else:
            passed = bool(ours.inspected)
        was = self._inspected
        self._inspected = passed
        if was is None or not self._enabled:
            return                                # first read is the baseline
        if passed and not was:
            self._fire_inspection()

    def _reset(self) -> None:
        self._queue_key = None
        self._inspected = None
        self.clear()

    # ── Firing ───────────────────────────────────────────────────────────

    def _fire_queue(self, stage: str, match: Match) -> None:
        team = nexus.our_team
        banner = queue_banner(stage, match, team)
        leds.start_alert(Alert(sides=STRIP_COLOR.get(banner.alliance or "", "#FFFFFF"),
                               centre_white=True, flash_s=QUEUE_FLASH_S,
                               steady_s=QUEUE_STEADY_S, hold=False,
                               label=banner.headline))
        self._set_banner(banner)
        self.log.emit(f"{banner.headline}: {banner.detail}")

    def _fire_inspection(self) -> None:
        banner = inspection_banner()
        leds.start_alert(Alert(sides=STRIP_GREEN, centre_white=True,
                               flash_s=INSPECTION_FLASH_S,
                               steady_s=INSPECTION_STEADY_S, hold=False,
                               label=banner.headline))
        self._set_banner(banner)
        self.log.emit("Inspection passed.")

    def _set_banner(self, banner: Banner | None) -> None:
        """Show `banner` for `BANNER_S`; None takes it down now."""
        self._banner_timer.stop()
        self._banner = banner
        self.banner_changed.emit(banner)
        if banner is not None:
            self._banner_timer.start(int(BANNER_S * 1000))

    # ── Operator controls ────────────────────────────────────────────────

    def clear(self) -> None:
        """Take down whatever is up — strips and banner."""
        leds.clear_alert()
        if self._banner is not None:
            self._set_banner(None)

    def test(self, kind: str) -> None:
        """Fire an alert by hand, to see the strips and the banner placement."""
        if kind == INSPECTION_PASSED:
            self._fire_inspection()
            return
        team = nexus.our_team
        match = nexus.next_match() or Match(
            label="Qualification 24",
            status=MatchState.NOW_QUEUING if kind == FIRST_QUEUE else MatchState.ON_DECK,
            red_teams=[team, "1234", "5678"], blue_teams=["100", "200", "300"],
            times=MatchTimes())
        self._fire_queue(kind, match)


alerts: _AlertService = LazyProxy("alerts", "init_alerts")  # type: ignore[assignment]


def init_alerts() -> _AlertService:
    """Call once in main(), after `init_nexus()` and `init_leds()`."""
    real = _AlertService()
    alerts._install(real)
    return real
