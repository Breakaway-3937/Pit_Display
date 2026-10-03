"""
The event's schedule: Screen B's half of the "Next match" set.

A shows the Nexus queue (the Next Match board); B shows every match at the
event in play order, **our matches marked**, results as they come in, and
**our record** once we have one (`app/event_schedule.py` builds the rows; the
pit-network page draws the same ones).

* **Our rows** sit on the plate's tile with our number set bold; our next
  match carries a white NEXT pill and the match on the field ON FIELD. Played
  matches dim. The window starts two before the first match still to play.
* **Zero red.** Red and blue are column names, not colours: a column of red
  type would be many red things on one surface, and red type on carbon fails.
* **Credits in the ledger:** "Event data from frc.nexus", and "Powered by
  The Blue Alliance" once TBA's results are on screen (`app/attribution.py`).
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen

from app import brand, event_schedule
from app.attribution import NEXUS_TEXT, TBA_TEXT
from app.config import config
from app.widgets.chassis import Chassis

_ROW = 72
_COLS = (("MATCH", 0.0), ("TIME", 0.20), ("RED", 0.32), ("BLUE", 0.56), ("RESULT", 0.80))


class ScheduleOverlay(Chassis):

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        try:
            from app.nexus import nexus
            # Bound methods: this face dies with its screen; the feed doesn't.
            nexus.status_changed.connect(self._on_feed)
            nexus.event_key_changed.connect(self._on_feed)
        except RuntimeError:
            pass

    def _on_feed(self, *_args) -> None:
        self.update()

    def _data(self) -> dict:
        return event_schedule.build(str(config.active_team.number))

    # ── Chassis hooks ─────────────────────────────────────────────────────

    def header_right(self) -> list[tuple]:
        side = "A" if self.screen_id.endswith("_a") else "B"
        live = bool(self._data()["rows"])
        return [("dot", brand.STATUS_ONLINE if live else brand.STATUS_IDLE, live),
                ("mono", f"SCREEN {side}  /  OUR MATCHES")]

    def footer_items(self) -> tuple[str, str]:
        d = self._data()
        right = NEXUS_TEXT.upper()
        if d["has_results"]:
            right += "  ·  RESULTS " + TBA_TEXT.upper()
        return "OUR MATCHES TODAY", right

    def paint_stage(self, p: QPainter, rect: QRectF):
        d = self._data()
        rows = d["rows"]
        if not rows:
            self._paint_empty(p, rect)
            return
        team = str(config.active_team.number)
        x0, w = rect.x(), rect.width()
        y = rect.y()
        if d["record"] is not None:
            wins, losses, ties = d["record"]
            p.setFont(self.display(24, 600, 0.16))
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(x0, y + p.fontMetrics().ascent()), "OUR RECORD")
            p.setFont(self.display(40, 700))
            p.setPen(QColor(self.ink))
            p.drawText(QPointF(x0 + self.s(210), y + self.s(30)), f"{wins}–{losses}–{ties}")
            y += self.s(62)
        p.setFont(self.display(22, 600, 0.16))
        p.setPen(QColor(self.muted))
        for title, at in _COLS:
            p.drawText(QPointF(x0 + w * at, y + p.fontMetrics().ascent()), title)
        y += self.s(44)

        fit = max(1, int((rect.bottom() - y) // self.s(_ROW)))
        start = event_schedule.window_start(rows)
        for r in rows[start:start + fit]:
            self._paint_row(p, QRectF(x0, y, w, self.s(_ROW)), r, team)
            y += self.s(_ROW)

    def _paint_row(self, p: QPainter, row: QRectF, r: dict, team: str) -> None:
        x0, w = row.x(), row.width()
        if r["ours"]:
            path = QPainterPath()
            path.addRoundedRect(row.adjusted(-self.s(12), self.s(4), self.s(12), -self.s(4)),
                                self.s(brand.R_BTN), self.s(brand.R_BTN))
            p.fillPath(path, QColor(self.tile))
        else:
            p.setPen(QPen(QColor(self.rule), self.m("rule")))
            p.drawLine(QPointF(x0, row.y()), QPointF(x0 + w, row.y()))
        dim = r["played"] and not r["current"]
        ink = self.faint if dim else self.ink
        body = self.faint if dim else self.body_ink
        mid = row.center().y()

        def text(x: float, s: str, font, colour) -> float:
            p.setFont(font)
            p.setPen(QColor(colour))
            fm = p.fontMetrics()
            p.drawText(QPointF(x, mid + (fm.ascent() - fm.descent()) / 2), s)
            return fm.horizontalAdvance(s)

        lw = text(x0, r["short"], self.display(32, 700), ink)
        tag = "NEXT" if r["next"] else "ON FIELD" if r["current"] else ""
        if tag:
            self._paint_pill(p, x0 + lw + self.s(16), mid, tag)
        stamp = datetime.fromtimestamp(r["at_ms"] / 1000) if r["at_ms"] else None
        text(x0 + w * 0.20, stamp.strftime("%H:%M") if stamp else "—", self.mono(28, 500), body)
        for key, at in (("red", 0.32), ("blue", 0.56)):
            x = x0 + w * at
            for t in r[key]:
                is_us = t == team
                x += text(x, t, self.mono(28, 800 if is_us else 500),
                          ink if is_us else body) + self.s(22)
        if r["result"]:
            res = r["result"]
            score = f"{res['red']}–{res['blue']}"
            text(x0 + w * 0.80, (f"{r['outcome']}  " if r["outcome"] else "") + score,
                 self.mono(28, 700 if r["outcome"] else 500), ink if r["outcome"] else body)

    def _paint_pill(self, p: QPainter, x: float, mid: float, label: str) -> None:
        """A white pill: the chosen-thing mark, never red."""
        p.setFont(self.display(17, 700, 0.14))
        tw = p.fontMetrics().horizontalAdvance(label)
        h = self.s(32)
        r = QRectF(x, mid - h / 2, tw + self.s(26), h)
        path = QPainterPath()
        path.addRoundedRect(r, h / 2, h / 2)
        p.fillPath(path, QColor(self.ink))
        p.setPen(QColor(brand.CARBON if self.dark else brand.WHITE))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), label)

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()), "OUR MATCHES")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "No matches yet", self.display(108, 700, -0.02),
                              self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Our matches appear here once the event's schedule is out.",
            self.body(32), self.body_ink, 1.4)
