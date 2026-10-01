"""
The Analysis board: the newest published analysis, on an overhead screen.

**The diagnostics board's painter, given the model's board.** A board
(`home/contracts/board.schema.json`) mirrors `diagnostics.Dashboard`, so this
subclasses `DiagnosticsOverlay` and changes only what differs:

* **the headline** is the board's own (title and sentence chosen by the
  designer, status set by code from the worst finding), not computed here;
* **the charts** the designer picked get a band under the vitals: a *line*
  across the match, or *bars* across devices, their data fetched by code
  through the same tools (`pipeline._chart_data`);
* **the ledger** says what read it and that it was checked.

The board's rules carry over: red marks a latched fault and its origin only
(the pipeline allows one red region), charts are never red, and nothing
animates. Pinned content (`content = "analysis"`), never in the rotation.
Nothing here computes a figure.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen

from app import brand
from app.ai import boards
from app.robot import diagnostics as dg
from app.widgets.diagnostics_overlay import DiagnosticsOverlay

_CHART_GAP = 20
_VITALS_H = 250          # vitals tiles above charts: label, figure, detail, a trace
_MAX_BARS = 8
_CHARTS_MIN = 190
_BAR_ROW = 40            # one bar's row, at least; fewer bars rather than crushed ones


class AnalysisOverlay(DiagnosticsOverlay):

    def __init__(self, screen_id: str = "", parent=None):
        self._board: boards.Board | None = None
        super().__init__(screen_id=screen_id, parent=parent)
        try:
            from app.ai.service import analysis
            # Bound methods: this face dies with its screen; the service doesn't.
            analysis.run_finished.connect(self._on_run_finished)
            analysis.runs_changed.connect(self.reload)
        except RuntimeError:
            pass                        # no analysis service (a test): reload by hand

    def _on_run_finished(self, _run_id: int, _status: str) -> None:
        self.reload()

    def reload(self):
        try:
            self._board = boards.latest()
        except Exception:
            self._board = None          # a bad row must never take a screen down
        self._data = self._board.dashboard if self._board else dg.Dashboard()
        self.update()

    # ── Chassis hooks ─────────────────────────────────────────────────────

    def header_right(self) -> list[tuple]:
        side = "A" if self.screen_id.endswith("_a") else "B"
        live = self._board is not None
        return [("dot", brand.STATUS_ONLINE if live else brand.STATUS_IDLE, live),
                ("mono", f"SCREEN {side}  /  ANALYSIS")]

    def footer_items(self) -> tuple[str, str]:
        b = self._board
        if b is None:
            return "NO ANALYSIS YET", ""
        model = (b.spec.get("models") or {}).get("analyst", "")
        left = "  ·  ".join(x for x in ("CHECKED AGAINST THE LOG",
                                         f"READ BY {model.upper()}" if model else "") if x)
        when = ""
        stamp = b.spec.get("generated_at") or ""
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")) \
                .astimezone().strftime("%H:%M")
        except ValueError:
            pass
        right = " · ".join(x for x in (b.spec.get("title", ""), when) if x)
        return left, right

    # ── The head: the board's own words ──────────────────────────────────

    def _headline(self) -> tuple[str, str, str, int]:
        h = self._board.headline if self._board else {}
        reds = sum(1 for r in self._data.vitals + self._data.faults if r.status == dg.FAULT)
        return (h.get("status") or dg.IDLE, h.get("title") or "Analysis",
                h.get("sentence") or "", reds or 1)

    def _paint_empty(self, p: QPainter, rect: QRectF):
        y = rect.y() + rect.height() * 0.18
        p.setFont(self.display(24, 600, 0.16))
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()), "NO ANALYSIS YET")
        y = self.draw_wrapped(p, rect.x(), y + self.s(60), rect.width(),
                              "Waiting for a board", self.display(108, 700, -0.02),
                              self.ink, 1.0)
        self.draw_wrapped(
            p, rect.x(), y + self.s(24), min(self.s(1200), rect.width()),
            "Each robot log is read by the pit's own model. Its board appears here "
            "once every figure on it has been checked against the log.",
            self.body(32), self.body_ink, 1.4)

    # ── The stage: head, vitals, charts, subsystems ──────────────────────

    def paint_stage(self, p: QPainter, rect: QRectF):
        charts = self._board.charts if self._board else []
        if self._data.empty or not charts:
            super().paint_stage(p, rect)
            return
        head_h = self.s(44 + 150 + 44)
        self._paint_head(p, QRectF(rect.x(), rect.y(), rect.width(), head_h))
        subs = self._subsystems()
        strip_h = self.s(150 + 26 + 24) if subs else 0.0
        grid = QRectF(rect.x(), rect.y() + head_h, rect.width(),
                      rect.height() - head_h - strip_h)
        if self._data.vitals:
            # The tiles keep their full height; the charts take what's left, down
            # to a floor that still reads.
            vit_h = min(self.s(_VITALS_H),
                        grid.height() - self.s(_CHARTS_MIN) - self.s(_CHART_GAP))
            if vit_h > self.s(120):
                self._paint_vitals(p, QRectF(grid.x(), grid.y(), grid.width(), vit_h))
            band = QRectF(grid.x(), grid.y() + vit_h + self.s(_CHART_GAP),
                          grid.width(), grid.height() - vit_h - self.s(_CHART_GAP))
        else:
            band = grid
        if band.height() > self.s(140):
            self._paint_charts(p, band, charts)
        if subs:
            self._paint_strip(p, QRectF(rect.x(), rect.bottom() - strip_h + self.s(24),
                                        rect.width(), strip_h - self.s(24)), subs)

    def _paint_charts(self, p: QPainter, rect: QRectF, charts: list[dict]):
        cols = max(1, min(len(charts), 2, int(rect.width() // self.s(520)) or 1))
        gap = self.s(_CHART_GAP)
        w = (rect.width() - gap * (cols - 1)) / cols
        for i, chart in enumerate(charts[:cols]):
            self._paint_chart(p, QRectF(rect.x() + i * (w + gap), rect.y(), w,
                                        rect.height()), chart)

    def _paint_chart(self, p: QPainter, rect: QRectF, chart: dict):
        r = self.s(14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.tile))
        p.drawRoundedRect(rect, r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(self.rule), max(1.0, self.s(1.5))))
        p.drawRoundedRect(rect, r, r)

        pad = self.s(30)
        x, w = rect.x() + pad, rect.width() - 2 * pad
        y = rect.y() + self.s(26)
        p.setFont(self.display(22, 600, 0.14))
        p.setPen(QColor(self.muted))
        fm = p.fontMetrics()
        title = chart.get("title", "").upper()
        unit = chart.get("unit") or ""
        p.drawText(QPointF(x, y + fm.ascent()),
                   fm.elidedText(title + (f"  ·  {unit}" if unit else ""),
                                 Qt.TextElideMode.ElideRight, int(w)))
        body = QRectF(x, y + fm.height() + self.s(18), w,
                      rect.bottom() - (y + fm.height() + self.s(18)) - self.s(24))
        data = chart.get("data") or {}
        if chart.get("kind") == "bars" and data.get("bars"):
            self._paint_bars(p, body, data["bars"])
        elif data.get("points"):
            self._paint_line(p, body, data["points"])
        elif data.get("sessions"):
            self._paint_bars(p, body, [{"label": s["session_uid"][:8], "value": s["max"]}
                                       for s in data["sessions"] if s.get("max") is not None])

    def _paint_line(self, p: QPainter, rect: QRectF, points: list):
        vals = [v for _t, v in points]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        t0, t1 = points[0][0], points[-1][0]
        tspan = (t1 - t0) or 1.0
        label_w = self.s(110)
        plot = QRectF(rect.x() + label_w, rect.y(), rect.width() - label_w,
                      rect.height() - self.s(34))
        p.setFont(self.mono(20, 500))
        p.setPen(QColor(self.faint))
        fm = p.fontMetrics()
        for v, yy in ((hi, plot.y()), (lo, plot.bottom())):
            p.drawText(QPointF(rect.x(), yy + fm.ascent() / 2), f"{v:,.4g}")
            p.setPen(QPen(QColor(self.rule), self.m("rule")))
            p.drawLine(QPointF(plot.x(), yy), QPointF(plot.right(), yy))
            p.setPen(QColor(self.faint))
        p.drawText(QPointF(plot.x(), rect.bottom()), f"{t0:,.0f} s")
        end = f"{t1:,.0f} s"
        p.drawText(QPointF(plot.right() - fm.horizontalAdvance(end), rect.bottom()), end)
        path = QPainterPath()
        for i, (t, v) in enumerate(points):
            px = plot.x() + plot.width() * (t - t0) / tspan
            py = plot.bottom() - (v - lo) / span * plot.height()
            path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(brand.N300 if self.dark else brand.N600),
                      max(1.0, self.s(3)), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)

    def _paint_bars(self, p: QPainter, rect: QRectF, bars: list[dict]):
        # Worst first upstream, so what the room cuts is the healthy end.
        fits = max(1, int(rect.height() // self.s(_BAR_ROW)))
        rows = bars[:min(_MAX_BARS, fits)]
        if not rows:
            return
        top = max(abs(b["value"]) for b in rows) or 1.0
        row_h = rect.height() / len(rows)
        bar_h = min(self.s(26), row_h * 0.55)
        label_w = min(self.s(320), rect.width() * 0.38)
        val_w = self.s(110)
        f_lab, f_val = self.body(22), self.mono(22, 500)
        for i, b in enumerate(rows):
            cy = rect.y() + row_h * (i + 0.5)
            p.setFont(f_lab)
            fm = p.fontMetrics()
            p.setPen(QColor(self.body_ink))
            p.drawText(QPointF(rect.x(), cy + fm.ascent() / 2 - fm.descent() / 2),
                       fm.elidedText(str(b["label"]), Qt.TextElideMode.ElideRight,
                                     int(label_w - self.s(16))))
            track = QRectF(rect.x() + label_w, cy - bar_h / 2,
                           rect.width() - label_w - val_w, bar_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.rule))
            p.drawRoundedRect(track, bar_h / 2, bar_h / 2)
            fill = QRectF(track.x(), track.y(),
                          max(bar_h, track.width() * abs(b["value"]) / top), bar_h)
            p.setBrush(QColor(brand.N300 if self.dark else brand.N600))
            p.drawRoundedRect(fill, bar_h / 2, bar_h / 2)
            p.setFont(f_val)
            vfm = p.fontMetrics()
            text = f"{b['value']:,.4g}"
            p.setPen(QColor(self.ink))
            p.drawText(QPointF(rect.right() - vfm.horizontalAdvance(text),
                               cy + vfm.ascent() / 2 - vfm.descent() / 2), text)
