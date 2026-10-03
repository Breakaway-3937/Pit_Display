"""
"Breakaway season by season": home's `bk_seasons` dataset as a time series.

Screen B's half of the "Team stats" set (`app/overhead.py`; A is the Quality
Award leaderboard). One column per season, 2012 → now (home sends them newest
first; the chart runs oldest to newest), as home/REQUESTS.md R11 asks:

* **Stacked bars**: wins (ink), losses (graphite), ties (faint), against the
  left axis (matches).
* **Win % as a line** on its own right axis, 0–100 %.
* **Best finish as a marker** over each bar: a filled diamond for "won an
  event", a hollow one for "finalist"; the legend says so.
* **The robot's name** under its year when the season had one.
* **A null record is a gap, never a zero**: 2021 (a remote season) shows its
  year and "remote" and no bar. 2015's few W/L (an average-score game) gets a
  footnote.
* **Not plotted: `high_score`, `best_opr`.** Every game scores differently,
  so a shared axis across years would mislead (home's caveat). Awards, which
  do compare, run as a row of numbers under the years.
* **Zero red**: no season is the one focus. "Powered by The Blue Alliance" in
  the ledger.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen

from app import brand, datasets
from app.attribution import TBA_TEXT
from app.widgets.chassis import Chassis

WON = "won an event"
FINALIST = "finalist"


def seasons(d: datasets.Dataset) -> list[dict]:
    """The rows as one dict per season, oldest first."""
    out = []
    for row in d.rows:
        year = d.value(row, "year")
        if not isinstance(year, (int, float)):
            continue
        pct = d.value(row, "win_pct")
        if isinstance(pct, (int, float)) and pct <= 1.0:
            pct = pct * 100.0                 # a fraction, not a percent
        out.append({
            "year": int(year), "robot": d.value(row, "robot_name") or "",
            "wins": d.value(row, "wins"), "losses": d.value(row, "losses"),
            "ties": d.value(row, "ties"), "pct": pct,
            "finish": str(d.value(row, "best_finish") or "").strip().lower(),
            "awards": d.value(row, "awards"),
        })
    return sorted(out, key=lambda s: s["year"])


def has_record(s: dict) -> bool:
    return any(isinstance(s[k], (int, float)) for k in ("wins", "losses", "ties"))


class SeasonsOverlay(Chassis):

    KEY = "bk_seasons"

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._data: datasets.Dataset | None = None
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
                ("mono", f"SCREEN {side}  /  SEASON BY SEASON")]

    def footer_items(self) -> tuple[str, str]:
        return "SEASON BY SEASON", TBA_TEXT.upper()

    def paint_stage(self, p: QPainter, rect: QRectF):
        d = self._data
        ss = seasons(d) if d is not None else []
        if not ss:
            self._paint_empty(p, rect)
            return
        x, y, w = rect.x(), rect.y(), rect.width()
        p.setFont(self.display(44, 700))
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(x, y + p.fontMetrics().ascent()), d.title)
        self._paint_legend(p, x + w, y + self.s(26))
        y += self.s(74)

        notes = []
        if any(not has_record(s) for s in ss):
            notes.append("A gap is a season with no record (2021 was played remotely).")
        if any(s["year"] == 2015 for s in ss):
            notes.append("2015 ranked by average score, so it has few wins or losses.")
        foot_h = self.s(30) * len(notes)
        labels_h = self.s(26 + 24 + 36 + 34)     # year, robot, finish, awards
        plot = QRectF(x + self.s(64), y, w - self.s(64 + 84),
                      rect.bottom() - y - labels_h - foot_h - self.s(16))

        most = max((sum(s[k] or 0 for k in ("wins", "losses", "ties")) for s in ss), default=1)
        top = max(10, ((int(most) + 9) // 10) * 10)
        self._paint_axes(p, plot, top)

        slot = plot.width() / len(ss)
        bar_w = min(self.s(64), slot * 0.56)
        line_pts = []
        for i, s in enumerate(ss):
            cx = plot.x() + slot * (i + 0.5)
            if has_record(s):
                base = plot.bottom()
                for key, colour in (("wins", self.ink),
                                    ("losses", brand.GRAPHITE if self.dark else brand.N300),
                                    ("ties", self.faint)):
                    n = s[key] or 0
                    if not n:
                        continue
                    h = plot.height() * n / top
                    p.fillRect(QRectF(cx - bar_w / 2, base - h, bar_w, h), QColor(colour))
                    base -= h
                if isinstance(s["pct"], (int, float)):
                    line_pts.append(QPointF(cx, plot.bottom() - plot.height() * s["pct"] / 100.0))
                else:
                    line_pts.append(None)
            else:
                line_pts.append(None)
                p.setFont(self.mono(18, 500, 0.1))
                p.setPen(QColor(self.faint))
                tw = p.fontMetrics().horizontalAdvance("REMOTE")
                p.drawText(QPointF(cx - tw / 2, plot.bottom() - self.s(10)), "REMOTE")
            self._paint_labels(p, cx, plot.bottom(), s)

        # Win %: a line on its own axis, broken where a season has none.
        p.setPen(QPen(QColor(self.muted), self.s(3)))
        prev = None
        for pt in line_pts:
            if pt is not None and prev is not None:
                p.drawLine(prev, pt)
            prev = pt
        p.setBrush(QColor(self.muted))
        for pt in line_pts:
            if pt is not None:
                p.drawEllipse(pt, self.s(5), self.s(5))
        p.setBrush(Qt.BrushStyle.NoBrush)

        fy = rect.bottom() - foot_h + self.s(22)
        p.setFont(self.body(22))
        p.setPen(QColor(self.muted))
        for note in notes:
            p.drawText(QPointF(x, fy), note)
            fy += self.s(30)

    def _paint_axes(self, p: QPainter, plot: QRectF, top: int) -> None:
        p.setFont(self.mono(20, 500))
        for frac in (0.0, 0.5, 1.0):
            yy = plot.bottom() - plot.height() * frac
            p.setPen(QPen(QColor(self.rule), self.m("rule")))
            p.drawLine(QPointF(plot.x(), yy), QPointF(plot.right(), yy))
            p.setPen(QColor(self.faint))
            left = str(int(top * frac))
            lw = p.fontMetrics().horizontalAdvance(left)
            p.drawText(QPointF(plot.x() - lw - self.s(14), yy + self.s(7)), left)
            p.drawText(QPointF(plot.right() + self.s(14), yy + self.s(7)), f"{int(100 * frac)}%")
        p.setFont(self.display(18, 600, 0.14))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(plot.x() - self.s(64), plot.y() - self.s(14)), "MATCHES")
        mw = p.fontMetrics().horizontalAdvance("WIN %")
        p.drawText(QPointF(plot.right() + self.s(84) - mw, plot.y() - self.s(14)), "WIN %")

    def _paint_labels(self, p: QPainter, cx: float, y0: float, s: dict) -> None:
        def centred(text: str, font, colour, y: float) -> None:
            p.setFont(font)
            p.setPen(QColor(colour))
            tw = p.fontMetrics().horizontalAdvance(text)
            p.drawText(QPointF(cx - tw / 2, y), text)
        centred(str(s["year"]), self.mono(22, 600), self.body_ink, y0 + self.s(28))
        if s["robot"]:
            centred(str(s["robot"]), self.body(17), self.muted, y0 + self.s(52))
        # The best finish has a row of its own, clear of the bars and the line.
        if s["finish"] in (WON, FINALIST):
            self._marker(p, QPointF(cx, y0 + self.s(76)), filled=s["finish"] == WON)
        awards = s["awards"]
        centred("—" if awards is None else str(awards), self.display(26, 700),
                self.ink, y0 + self.s(120))

    def _marker(self, p: QPainter, c: QPointF, filled: bool) -> None:
        r = self.s(11)
        path = QPainterPath()
        path.moveTo(c.x(), c.y() - r)
        path.lineTo(c.x() + r, c.y())
        path.lineTo(c.x(), c.y() + r)
        path.lineTo(c.x() - r, c.y())
        path.closeSubpath()
        if filled:
            p.fillPath(path, QColor(self.ink))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(self.ink), self.s(2.5)))
            p.drawPath(path)

    def _paint_legend(self, p: QPainter, right: float, y: float) -> None:
        items = [("box", self.ink, "Wins"),
                 ("box", brand.GRAPHITE if self.dark else brand.N300, "Losses"),
                 ("line", self.muted, "Win %"),
                 ("won", self.ink, "Won an event"), ("fin", self.ink, "Finalist"),
                 ("text", self.ink, "Awards")]
        p.setFont(self.body(20))
        fm = p.fontMetrics()
        widths = [self.s(30) + fm.horizontalAdvance(t) + self.s(26) for *_x, t in items]
        x = right - sum(widths)
        for (kind, colour, label), wdt in zip(items, widths):
            mid = y
            if kind == "box":
                p.fillRect(QRectF(x, mid - self.s(8), self.s(18), self.s(16)), QColor(colour))
            elif kind == "line":
                p.setPen(QPen(QColor(colour), self.s(3)))
                p.drawLine(QPointF(x, mid), QPointF(x + self.s(20), mid))
            elif kind in ("won", "fin"):
                self._marker(p, QPointF(x + self.s(9), mid), filled=kind == "won")
            elif kind == "text":
                p.setFont(self.display(18, 700))
                p.setPen(QColor(colour))
                p.drawText(QPointF(x, mid + self.s(7)), "#")
                p.setFont(self.body(20))
            p.setPen(QColor(self.body_ink))
            p.drawText(QPointF(x + self.s(30), mid + (fm.ascent() - fm.descent()) / 2), label)
            x += wdt

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()), "SEASON BY SEASON")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Not on yet", self.display(108, 700, -0.02), self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Breakaway's seasons appear here once they arrive from home and an adult "
            "has turned them on.", self.body(32), self.body_ink, 1.4)
