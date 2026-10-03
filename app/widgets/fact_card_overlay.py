"""
Fun-fact cards: one big fact per screen, from home's datasets.

The "Fun facts" set (`app/overhead.py`): Screen A shows **Breakaway's records**
(`bk_records`) as figure cards, Screen B **"Did you know?"** sentences
(`fun_facts`) as statement cards; both turn every `CARD_PERIOD_S` by the
clock (`datasets.card()`), so the pair and the pit-network page agree.

* **A record card** is the figure archetype: the value as the numeral, fitted
  to the width (300 px at most), the record's name under it, home's detail
  as the caption. Zero red: the numeral is the focus, in ink.
* **A fact card** is the statement archetype: the Trace (its one red) into
  the sentence, fitted from 120 px down so a long one never runs off.
* Every word and number is home's. Nothing shows until an adult has cleared
  the dataset. "Powered by The Blue Alliance" in the ledger.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QPointF, QRectF, QTimer
from PyQt6.QtGui import QColor, QPainter

from app import brand, datasets
from app.attribution import TBA_TEXT
from app.widgets.brand_widgets import paint_trace
from app.widgets.chassis import Chassis


class FactCardOverlay(Chassis):

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._shown = None
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._maybe_turn)

    def _card(self) -> dict | None:
        return datasets.card(self.screen_id, time.time())

    def _maybe_turn(self) -> None:
        c = self._card()
        if c != self._shown:
            self._shown = c
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._tick.start()

    def hideEvent(self, event):
        self._tick.stop()
        super().hideEvent(event)

    def header_right(self) -> list[tuple]:
        side = "A" if self.screen_id.endswith("_a") else "B"
        what = "OUR RECORDS" if side == "A" else "DID YOU KNOW"
        return [("mono", f"SCREEN {side}  /  {what}")]

    def footer_items(self) -> tuple[str, str]:
        side_a = self.screen_id.endswith("_a")
        return ("BREAKAWAY'S RECORDS" if side_a else "DID YOU KNOW"), TBA_TEXT.upper()

    def paint_stage(self, p: QPainter, rect: QRectF):
        c = self._card()
        self._shown = c
        if c is None:
            self._paint_empty(p, rect)
        elif c["kind"] == "record":
            self._paint_record(p, rect, c)
        else:
            self._paint_fact(p, rect, c)

    def _paint_record(self, p: QPainter, rect: QRectF, c: dict) -> None:
        x, y, w = rect.x(), rect.y(), rect.width()
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(x, y + p.fontMetrics().ascent()), "BREAKAWAY'S RECORDS")
        y += self.s(48)
        px = 300
        while px > 96:
            p.setFont(self.display(px, 700, -0.03))
            if p.fontMetrics().horizontalAdvance(c["value"]) <= w:
                break
            px -= 12
        fm = p.fontMetrics()
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(x - self.s(6), y + fm.ascent() * 0.86), c["value"])
        y += fm.ascent() * 0.86 + self.s(36)
        y = self.draw_wrapped(p, x, y, min(self.s(1500), w), c["record"],
                              self.display(64, 700, -0.01), self.ink, 1.05)
        if c["detail"]:
            self.draw_wrapped(p, x, y + self.s(24), min(self.s(1300), w), c["detail"],
                              self.body(34), self.body_ink, 1.4)

    def _paint_fact(self, p: QPainter, rect: QRectF, c: dict) -> None:
        x, y, w = rect.x(), rect.y(), rect.width()
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        eyebrow = "DID YOU KNOW?" + ("  ·  ARKANSAS" if c["category"] == "arkansas" else "")
        p.drawText(QPointF(x, y + p.fontMetrics().ascent()), eyebrow)
        y += self.s(24 + 16)
        trace_w = self.s(400)
        paint_trace(p, x, y, trace_w, brand.RED, 1.0)
        y += trace_w * 62 / 300
        width = min(self.s(1500), w)
        px = 120
        while px > 64 and self.draw_wrapped(p, x, y, width, c["text"],
                                            self.display(px, 700, -0.018), self.ink,
                                            1.02, measure_only=True) > rect.bottom():
            px -= 8
        self.draw_wrapped(p, x, y, width, c["text"], self.display(px, 700, -0.018),
                          self.ink, 1.02)

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()), "FUN FACTS")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Not on yet", self.display(108, 700, -0.02), self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Breakaway's records and facts appear here once they arrive from home "
            "and an adult has turned them on.", self.body(32), self.body_ink, 1.4)
