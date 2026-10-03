"""
"Quality Award leaders": home's `quality` dataset as a ranked bar chart.

Screen A's half of the "Team stats" set (`app/overhead.py`). Brayden's own
view (`vw_we_be_quality`) and the team's most important dataset: Quality
Awards won by team, top 25 of every team that has one, Breakaway's row always
included (home/REQUESTS.md R11).

* **Two columns of ranked bars**, 13 and 12, so 25 rows read from the pit
  floor (a single column would be 26 px a row). A bar's length is its count
  against the leader's.
* **Ties show**: a rank shared by more than one row reads "T-1".
* **Breakaway's bar is the one red thing on the surface** (a filled shape, as
  the brand allows on dark); every other bar is graphite. Breakaway is found by
  home's `highlight`, never by guessing.
* Every number is home's. "Showing 25 of 1,186" from `total_rows`.
* "Powered by The Blue Alliance" in the ledger, as on every TBA surface.
"""

from __future__ import annotations

from collections import Counter

from PyQt6.QtCore import QPointF, QRectF, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath

from app import brand, datasets
from app.attribution import TBA_TEXT
from app.widgets.chassis import Chassis

_COL_GAP = 96


class QualityOverlay(Chassis):

    KEY = "quality"

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._data: datasets.Dataset | None = None
        # New rows arrive by sync; a slow look is plenty (nothing here moves).
        self._poll = QTimer(self)
        self._poll.setInterval(15_000)
        self._poll.timeout.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        self._data = datasets.get(self.KEY)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.reload()
        self._poll.start()

    def hideEvent(self, event):
        self._poll.stop()
        super().hideEvent(event)

    def header_right(self) -> list[tuple]:
        side = "A" if self.screen_id.endswith("_a") else "B"
        live = self._data is not None
        return [("dot", brand.STATUS_ONLINE if live else brand.STATUS_IDLE, live),
                ("mono", f"SCREEN {side}  /  QUALITY AWARDS")]

    def footer_items(self) -> tuple[str, str]:
        return "QUALITY AWARD LEADERS", TBA_TEXT.upper()

    def paint_stage(self, p: QPainter, rect: QRectF):
        d = self._data
        if d is None or not d.rows:
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
        y += self.s(20)

        ranks = [d.value(r, "quality_rank") for r in d.rows]
        shared = Counter(ranks)
        counts = [d.value(r, "top_quality") or 0 for r in d.rows]
        top = max([c for c in counts if isinstance(c, (int, float))] or [1])
        rows = list(enumerate(d.rows))
        half = (len(rows) + 1) // 2
        col_w = (w - self.s(_COL_GAP)) / 2
        height = rect.bottom() - y
        row_h = min(self.s(64), height / max(1, half))
        for c, chunk in enumerate((rows[:half], rows[half:])):
            cx = x + c * (col_w + self.s(_COL_GAP))
            for k, (i, row) in enumerate(chunk):
                self._paint_row(p, QRectF(cx, y + k * row_h, col_w, row_h), d, i, row,
                                ranks[i], shared[ranks[i]] > 1, counts[i], top)

    def _paint_row(self, p: QPainter, r: QRectF, d: datasets.Dataset, i: int, row: tuple,
                   rank, tied: bool, count, top) -> None:
        ours = d.is_highlight(i)
        mid = r.center().y()

        def text(x: float, s: str, font, colour) -> float:
            p.setFont(font)
            p.setPen(QColor(colour))
            fm = p.fontMetrics()
            p.drawText(QPointF(x, mid + (fm.ascent() - fm.descent()) / 2), s)
            return fm.horizontalAdvance(s)

        rank_s = (f"T-{rank}" if tied else str(rank)) if rank is not None else ""
        text(r.x(), rank_s, self.mono(24, 600), self.ink if ours else self.muted)
        team = datasets.team_number(d.value(row, "team_key"))
        tw = text(r.x() + self.s(84), team, self.display(30, 700),
                  self.ink if ours else self.body_ink)
        nick = datasets.nickname(team)
        if nick:
            text(r.x() + self.s(84) + tw + self.s(12), nick, self.body(20),
                 self.muted)
        bar_x = r.x() + self.s(84 + 150)
        bar_w = r.right() - bar_x - self.s(64)
        if isinstance(count, (int, float)) and top:
            h = max(self.s(10), r.height() * 0.36)
            length = max(h, bar_w * float(count) / float(top))
            path = QPainterPath()
            path.addRoundedRect(QRectF(bar_x, mid - h / 2, length, h), h / 2, h / 2)
            p.fillPath(path, QColor(brand.RED if ours else
                                    (brand.GRAPHITE if self.dark else brand.N300)))
            text(bar_x + length + self.s(14), datasets.cell(count), self.mono(26, 700),
                 self.ink if ours else self.body_ink)

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()), "QUALITY AWARDS")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Not on yet", self.display(108, 700, -0.02), self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "The Quality Award leaderboard appears here once it arrives from home "
            "and an adult has turned it on.", self.body(32), self.body_ink, 1.4)
