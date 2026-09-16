"""
Next Match — the board that answers *when do we go?*

Pinned content on either overhead screen (`content = "next_match"`), on the
shared chassis like every other face. It reads the Nexus feed and nothing
else; if the feed is off, it says so and how to turn it on.

## The glance, in order

1. **The countdown** — a 236px mono numeral on the stage's centre axis:
   `12:34` to the next thing that happens to us. Which thing is the eyebrow
   above it (`QUEUE IN`, `ON DECK IN`, `ON FIELD IN`, `STARTS IN`), chosen
   from the match's own status so it always names the *next* step, never one
   that has already happened.
2. **The match** — its label, and the alliance seal with our station.
3. **The timeline** — the four estimates in a row, the next one lit.
4. **Who** — with whom, against whom, in station order.

A crew member across the pit stops at step one. Somebody at the bench reads
to four.

## What the numbers are

Every time here is Nexus's *estimate*, recomputed by the lead queuer's
tablet every time anything moves, and it moves — by seconds between polls,
by minutes after a field fault. The countdown ticks once a second against
the estimate we hold; the estimate itself only changes when a new snapshot
does. Past the estimate it reads `NOW`, because "−2:14" is not a thing a
crew can act on and the estimate has by then been overtaken by the call
itself. At an event that does not queue with Nexus the estimates never
move; there is no flag for that and the board cannot tell.

## Colour

**The whole plate is the alliance colour.** Red card, blue card — readable
from the far side of the pit before a single word is, which is the point of
a board that says *when do we go*. Every ink goes to white on it (§03: on a
red ground, red is replaced by white), and the seal reverses — white fill,
alliance-colour type — because a red pill on a red card is nothing. The
seal is then the one shape that is not white, and the card itself is the
surface's one red. With no match to show, the plate is the theme's own.

Attribution is a condition of the API's use, so the ledger says where the
data came from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen

from app import brand
from app.config import config
from app.nexus import nexus
from app.nexus.api import ATTRIBUTION, Match, MatchState, when
from app.widgets.chassis import Chassis

_ALLIANCE_FILL = {"red": brand.RED, "blue": brand.SKY}

# (eyebrow over the countdown, which estimate it counts to), by match status.
_NEXT_STEP = {
    MatchState.QUEUING_SOON: ("QUEUE IN",    "estimated_queue"),
    MatchState.NOW_QUEUING:  ("ON DECK IN",  "estimated_on_deck"),
    MatchState.ON_DECK:      ("ON FIELD IN", "estimated_on_field"),
    MatchState.ON_FIELD:     ("STARTS IN",   "estimated_start"),
}

_TIMELINE = (("QUEUE", "estimated_queue", MatchState.NOW_QUEUING),
             ("ON DECK", "estimated_on_deck", MatchState.ON_DECK),
             ("ON FIELD", "estimated_on_field", MatchState.ON_FIELD),
             ("START", "estimated_start", None))


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _clock(ms: int | None) -> str:
    dt = when(ms)
    return dt.strftime("%H:%M") if dt else "--:--"


def _countdown(ms: int | None) -> str:
    """`12:34`, `1h 05m`, or `NOW` once the estimate has passed."""
    if ms is None:
        return "--:--"
    delta = (ms - _now_ms()) / 1000
    if delta <= 0:
        return "NOW"
    if delta >= 3600:
        return f"{int(delta // 3600)}h {int(delta % 3600 // 60):02d}m"
    return f"{int(delta // 60)}:{int(delta % 60):02d}"


class NextMatchOverlay(Chassis):
    """Either screen's *when do we go* board, on the shared chassis."""

    # The ledger carries the clock, so it is read, not glanced past.
    FOOTER_PX = 24

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        # One repaint a second for the countdown, and only while showing —
        # a hidden page ticking a full-chassis repaint is pure waste.
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self.update)
        # Bound methods, never lambdas. This widget is destroyed when its
        # screen is powered off, and PyQt only auto-disconnects a slot that
        # is a method of a QObject — a lambda keeps firing into a deleted
        # widget and the next poll takes the whole app down.
        nexus.status_changed.connect(self._repaint)
        nexus.match_changed.connect(self._repaint)
        nexus.state_changed.connect(self._repaint)
        nexus.pits_changed.connect(self._repaint)
        config.team_changed.connect(self._repaint)

    def _repaint(self, *_args):
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._tick.start()

    def plate_tint(self) -> str | None:
        """The alliance colour, once there is a match of ours to show."""
        if not nexus.configured or nexus.status is None:
            return None
        match = nexus.next_match() or nexus.current_match()
        if match is None:
            return None
        return _ALLIANCE_FILL.get(match.alliance_of(nexus.our_team) or "")

    def hideEvent(self, event):
        super().hideEvent(event)
        self._tick.stop()

    # ── Chassis hooks ─────────────────────────────────────────────────────

    def header_right(self) -> list[tuple]:
        live = nexus.state == "live"
        label = "SCREEN A" if self.screen_id == "presentation_a" else "SCREEN B"
        return [("dot", brand.STATUS_ONLINE if live else brand.STATUS_IDLE, live),
                ("mono", f"{label}  /  NEXT MATCH")]

    def footer_items(self) -> tuple[str, str]:
        # The clock leads the ledger: every estimate on the stage is a time of
        # day, and a countdown is only half the answer without the other half.
        clock = datetime.now().strftime("%H:%M:%S")
        status = nexus.status
        if status is None:
            return f"{clock}  ·  NO EVENT FEED", ATTRIBUTION.upper()
        left = [clock,
                f"NOW QUEUING {status.now_queuing.upper()}" if status.now_queuing
                else "NOTHING QUEUING"]
        pit = nexus.our_pit()
        if pit:
            left.append(f"PIT {pit}")
        stamp = when(status.data_as_of)
        left.append(f"DATA AS OF {stamp.strftime('%H:%M:%S') if stamp else '?'}")
        return "  ·  ".join(left), ATTRIBUTION.upper()

    # ── The stage ─────────────────────────────────────────────────────────

    def paint_stage(self, p: QPainter, rect: QRectF):
        if not nexus.configured:
            self._paint_empty(p, rect, "No event feed",
                              "Set the event and the Nexus key in Control → Pit "
                              "Systems → Event Feed and this board fills itself in.")
            return
        status = nexus.status
        if status is None:
            self._paint_empty(p, rect, "Waiting for Nexus",
                              nexus.message or "Fetching the event…")
            return
        team = nexus.our_team
        match = nexus.next_match()
        if match is None:
            current = nexus.current_match()
            if current is not None:
                self._paint_match(p, rect, current, team, played=True)
                return
            if not status.matches:
                self._paint_empty(p, rect, "No schedule yet",
                                  "The event has not published its matches. "
                                  "This board fills in the moment it does.")
            else:
                self._paint_empty(p, rect, f"Team {team} has no match scheduled",
                                  "Nothing left in the schedule for us. If this is "
                                  "playoffs, alliances may not be set yet.")
            return
        self._paint_match(p, rect, match, team, played=False)

    def _paint_empty(self, p: QPainter, rect: QRectF, head: str, body: str):
        cx = rect.center().x()
        y = rect.y() + rect.height() * 0.22
        f_eye = self.display(24, 600, 0.16)
        self._centred(p, cx, y + QFontMetricsF(f_eye).ascent(), "NEXT MATCH",
                      f_eye, self.muted)
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              head, self.display(108, 700, -0.02), self.ink, 1.0,
                              align="center")
        w = min(self.s(1200), rect.width())
        self.draw_wrapped(p, cx - w / 2, y + self.s(24), w,
                          body, self.body(32), self.body_ink, 1.4, align="center")

    # ── A match ───────────────────────────────────────────────────────────
    #
    # Everything is centred on the stage's own axis. A board read from
    # across a pit is read down its middle — the eye lands on the numeral
    # and works outward — and a left-ranged layout on a 55" panel left the
    # right half of the plate empty. The timeline and the two team rows
    # spread across the full width so nothing is stranded.

    def _centred(self, p: QPainter, cx: float, baseline: float, text: str,
                 font, colour: str) -> float:
        p.setFont(font)
        p.setPen(QColor(colour))
        w = QFontMetricsF(font).horizontalAdvance(text)
        p.drawText(QPointF(cx - w / 2, baseline), text)
        return w

    def _paint_match(self, p: QPainter, rect: QRectF, m: Match, team: str,
                     played: bool):
        cx = rect.center().x()
        y = rect.y()
        side = m.alliance_of(team)
        station = 0
        if side:
            teams = m.red_teams if side == "red" else m.blue_teams
            station = (teams or []).index(team) + 1

        # ── Eyebrow ───────────────────────────────────────────────────────
        f_eye = self.display(24, 600, 0.16)
        eyebrow = "LAST MATCH" if played else "NEXT MATCH"
        if m.replay_of:
            eyebrow += f"  ·  REPLAY OF {m.replay_of.upper()}"
        self._centred(p, cx, y + QFontMetricsF(f_eye).ascent(), eyebrow,
                      f_eye, self.muted)
        y += self.s(34)

        # ── Match label + alliance seal, centred as one group ─────────────
        f_label = self.display(96, 700, -0.02)
        fm = QFontMetricsF(f_label)
        label_w = fm.horizontalAdvance(m.label)
        seal_w = seal_h = 0.0
        f_seal = self.display(30, 700, 0.06)
        sm = QFontMetricsF(f_seal)
        seal_text = f"{side.upper()}  {station}" if side else ""
        if side:
            seal_h = sm.height() + self.s(22)
            seal_w = sm.horizontalAdvance(seal_text) + self.s(48)
        gap = self.s(36) if side else 0.0
        group_w = label_w + gap + seal_w
        x = cx - group_w / 2
        p.setFont(f_label)
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(x, y + fm.ascent()), m.label)
        if side:
            # Reversed: white pill, alliance-colour type, on the tinted plate.
            r = QRectF(x + label_w + gap, y + fm.ascent() - seal_h + self.s(10),
                       seal_w, seal_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(brand.WHITE))
            p.drawRoundedRect(r, seal_h / 2, seal_h / 2)
            p.setFont(f_seal)
            p.setPen(QColor(_ALLIANCE_FILL[side]))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), seal_text)
        y += fm.height() - self.s(6)

        # ── The countdown ─────────────────────────────────────────────────
        step, field = _NEXT_STEP.get(m.status, ("STARTS IN", "estimated_start"))
        target = getattr(m.times, field)
        if played and m.times.actual_start:
            step, target = "STARTED", m.times.actual_start
        self._centred(p, cx, y + QFontMetricsF(f_eye).ascent(), step,
                      f_eye, self.muted)
        y += self.s(30)

        big = _clock(target) if step == "STARTED" else _countdown(target)
        f_big = self.mono(236, 700, -0.04)
        bm = QFontMetricsF(f_big)
        baseline = y + bm.ascent() - self.s(30)
        self._centred(p, cx, baseline, big, f_big, self.ink)
        y = baseline + self.s(12)

        if step != "STARTED" and target is not None:
            f_at = self.display(40, 500)
            self._centred(p, cx, y + QFontMetricsF(f_at).ascent(),
                          f"at {_clock(target)}", f_at, self.body_ink)
            y += QFontMetricsF(f_at).height()
        y += self.s(8)

        # ── The timeline, across the full stage ───────────────────────────
        y = self._paint_timeline(p, rect.x(), y, rect.width(), m)

        # ── Who, in two halves ────────────────────────────────────────────
        if rect.bottom() - y > self.s(76):
            self._paint_teams(p, rect.x(), y + self.s(16), rect.width(),
                              m, team, side)

    def _paint_timeline(self, p: QPainter, x: float, y: float, width: float,
                        m: Match) -> float:
        # Which stop is "next": the first whose state the match has not reached.
        rank = MatchState.rank(m.status)
        next_i = 0
        for i, (_label, _field, reached) in enumerate(_TIMELINE):
            if reached is not None and rank >= MatchState.rank(reached):
                next_i = i + 1
        if m.status == MatchState.ON_FIELD and m.times.actual_start:
            next_i = len(_TIMELINE)

        n = len(_TIMELINE)
        # Stops at the centres of n equal columns spanning the stage, the rail
        # running between the first and last stop.
        col_w = width / n
        centres = [x + col_w * (i + 0.5) for i in range(n)]
        f_lab = self.display(20, 600, 0.16)
        f_val = self.mono(44, 500)
        lm, vm = QFontMetricsF(f_lab), QFontMetricsF(f_val)

        rail_y = y + self.s(10)
        p.setPen(QPen(QColor(self.rule), self.s(2)))
        p.drawLine(QPointF(centres[0], rail_y), QPointF(centres[-1], rail_y))

        d = self.s(16)
        for i, (label, field, _reached) in enumerate(_TIMELINE):
            cx = centres[i]
            value = getattr(m.times, field)
            done = i < next_i
            lit = i == next_i
            ink = self.ink if lit else (self.faint if done else self.body_ink)
            p.setPen(QPen(QColor(ink), self.s(2)))
            p.setBrush(QColor(ink) if lit else QColor(self.plate_tint() or
                                                    (brand.CARBON_SURF if self.dark
                                                     else brand.WHITE)))
            p.drawEllipse(QPointF(cx, rail_y), d / 2, d / 2)
            self._centred(p, cx, rail_y + self.s(22) + lm.ascent(), label,
                          f_lab, self.ink if lit else self.muted)
            self._centred(p, cx, rail_y + self.s(22) + lm.height() + self.s(2)
                          + vm.ascent(), _clock(value), f_val, ink)
        return rail_y + self.s(22) + lm.height() + self.s(2) + vm.ascent() + self.s(8)

    def _paint_teams(self, p: QPainter, x: float, y: float, width: float,
                     m: Match, team: str, side: str | None):
        ours = (m.red_teams if side == "red" else m.blue_teams) if side else None
        them = (m.blue_teams if side == "red" else m.red_teams) if side else None
        if side:
            halves = [("WITH", [t for t in (ours or []) if t and t != team] or ["—"]),
                      ("VS", [t or "—" for t in (them or [])] or ["TBD"])]
        else:
            halves = [("RED", [t or "—" for t in (m.red_teams or [])] or ["TBD"]),
                      ("BLUE", [t or "—" for t in (m.blue_teams or [])] or ["TBD"])]

        f_lab = self.display(20, 600, 0.16)
        f_num = self.mono(36, 500)
        lm, nm = QFontMetricsF(f_lab), QFontMetricsF(f_num)
        for i, (label, teams) in enumerate(halves):
            cx = x + width * (0.25 if i == 0 else 0.75)
            self._centred(p, cx, y + lm.ascent(), label, f_lab, self.muted)
            self._centred(p, cx, y + lm.height() + self.s(4) + nm.ascent(),
                          "    ".join(teams), f_num, self.ink)
