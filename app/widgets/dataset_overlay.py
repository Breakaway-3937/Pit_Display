"""
Any of home's datasets, drawn the same way: the generic renderer (R10).

The "Datasets" set (`app/overhead.py`) pages through every cleared dataset
without a screen of its own, two at a time: Screen A the first of a pair,
Screen B the second, turning every `PERIOD_S` **by the clock**
(`datasets.page_pair()`), so both screens and the pit-network page agree with
nothing to keep in step.

* Title, description, "Showing 25 of 1,186"; then the table: column names as
  headers (`longest_win_streak` → "Longest win streak"; `team_key` hidden when
  `team_number` is there), rows in home's order.
* **Home's highlight rows** (ours) sit on the plate's tile, set bold. When
  the rows don't fit, the top ones show and ours is kept: never dropped.
* Zero red. "Powered by The Blue Alliance" in the ledger.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath

from app import brand, datasets
from app.attribution import TBA_TEXT
from app.widgets.chassis import Chassis

PERIOD_S = 30
_ROW = 54


def side_dataset(screen_id: str, now_s: float | None = None) -> datasets.Dataset | None:
    a, b = datasets.page_pair(time.time() if now_s is None else now_s, PERIOD_S)
    if screen_id.endswith("_b"):
        if b is not None:
            return b
        found = datasets.generic()
        return found[0] if len(found) > 1 else a       # odd count: wrap round
    return a


def rows_to_show(d: datasets.Dataset, fit: int) -> list[int]:
    """Row indexes that fit, keeping every highlight row (ours)."""
    n = len(d.rows)
    if n <= fit:
        return list(range(n))
    keep = sorted(i for i in d.highlight_rows if i < n)
    head = [i for i in range(n) if i not in keep][:max(0, fit - len(keep))]
    return sorted(set(head) | set(keep[:fit]))


class DatasetOverlay(Chassis):

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._key = ""
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._maybe_turn)

    def _maybe_turn(self) -> None:
        d = side_dataset(self.screen_id)
        key = d.key if d is not None else ""
        if key != self._key:
            self._key = key
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._tick.start()

    def hideEvent(self, event):
        self._tick.stop()
        super().hideEvent(event)

    def header_right(self) -> list[tuple]:
        side = "A" if self.screen_id.endswith("_a") else "B"
        return [("mono", f"SCREEN {side}  /  DATASETS")]

    def footer_items(self) -> tuple[str, str]:
        d = side_dataset(self.screen_id)
        return (d.title.upper() if d else "DATASETS"), TBA_TEXT.upper()

    def paint_stage(self, p: QPainter, rect: QRectF):
        d = side_dataset(self.screen_id)
        self._key = d.key if d is not None else ""
        if d is None or not d.columns:
            self._paint_empty(p, rect)
            return
        x, y, w = rect.x(), rect.y(), rect.width()
        p.setFont(self.display(44, 700))
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(x, y + p.fontMetrics().ascent()), d.title)
        if d.shown_of:
            p.setFont(self.mono(22, 500, 0.08))
            p.setPen(QColor(self.muted))
            sw = p.fontMetrics().horizontalAdvance(d.shown_of.upper())
            p.drawText(QPointF(x + w - sw, y + self.s(30)), d.shown_of.upper())
        y += self.s(58)
        if d.description:
            y = self.draw_wrapped(p, x, y, w, d.description, self.body(24), self.muted, 1.2)
        y += self.s(22)

        cols = datasets.visible_columns(d)
        # Width by content: the longest of header and values, capped.
        p.setFont(self.body(26))
        fm = p.fontMetrics()
        widths = []
        for i in cols:
            texts = [datasets.header(d.columns[i])] + [datasets.cell(r[i]) for r in d.rows
                                                       if i < len(r)]
            widths.append(min(self.s(560), max(fm.horizontalAdvance(t) for t in texts)))
        scale = w / max(1.0, sum(widths) + self.s(28) * (len(cols) - 1))
        xs, cx = [], x
        for wd in widths:
            xs.append(cx)
            cx += wd * min(scale, 3.0) + self.s(28)

        p.setFont(self.display(20, 600, 0.14))
        p.setPen(QColor(self.muted))
        for i, xx in zip(cols, xs):
            p.drawText(QPointF(xx, y + p.fontMetrics().ascent()), datasets.header(d.columns[i]).upper())
        y += self.s(40)
        fit = max(1, int((rect.bottom() - y) // self.s(_ROW)))
        for k in rows_to_show(d, fit):
            row = d.rows[k]
            ours = d.is_highlight(k)
            r = QRectF(x, y, w, self.s(_ROW))
            if ours:
                path = QPainterPath()
                path.addRoundedRect(r.adjusted(-self.s(12), self.s(3), self.s(12), -self.s(3)),
                                    self.s(brand.R_BTN), self.s(brand.R_BTN))
                p.fillPath(path, QColor(self.tile))
            else:
                p.setPen(QColor(self.rule))
                p.drawLine(QPointF(x, y), QPointF(x + w, y))
            p.setFont(self.body(26, 700 if ours else 400))
            p.setPen(QColor(self.ink if ours else self.body_ink))
            fm = p.fontMetrics()
            mid = r.center().y() + (fm.ascent() - fm.descent()) / 2
            for i, xx, wd in zip(cols, xs, widths):
                text = datasets.cell(row[i] if i < len(row) else None)
                p.drawText(QPointF(xx, mid), fm.elidedText(text, Qt.TextElideMode.ElideRight, int(wd * min(scale, 3.0))))
            y += self.s(_ROW)

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()), "DATASETS")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Nothing turned on", self.display(108, 700, -0.02), self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Home's datasets appear here once an adult has reviewed one and turned it on.",
            self.body(32), self.body_ink, 1.4)
