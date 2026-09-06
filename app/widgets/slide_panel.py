"""
SlidePanel — the audience rotation, painted on the shared chassis.

**One shell, four archetypes.** Screen A and Screen B are visible in the same
glance, so they run the same chassis (`app/widgets/chassis.py`) and differ by
exactly one thing: the ledger reads `ROTATION A` / `ROTATION B` and the rail
sits left on A, right on B. Enough to orient; not enough to look like two apps.

| archetype | content shape | where the one red goes |
|---|---|---|
| statement | authored headline + sentence | the **Trace** |
| figure | a real number out of a log | the **FROM LOG** seal |
| roster | a grid of sponsor marks | **nowhere** — the marks are the colour |

The archetype is chosen by the content, not by slide number — see `app/slides.py`.

**Everything is painted, not laid out.** The old panel was a `QStackedWidget`
of `QLabel`s, which is why it could only ever be one template and why the
transition could only be an instant swap. A 140px headline on a 55" panel also
has to be sized from the live widget height, and Qt's layout system cannot
express "1.0 line-height at 140px" at all. So the stage is one `paintEvent` and
the transition is a `QVariantAnimation` over a painted opacity/offset pair.

**The transition, per the design:** 260ms out (opacity→0, y −22, InCubic), 80ms
of empty stage, 420ms in (opacity→1, y +26→0, OutCubic), and the Trace redraws
over 900ms OutExpo behind it. The animation therefore runs 1240ms even though
the slide has settled at 760ms — the line is still arriving.

**The rail never resets mid-slide.** It reads `rotation.progress()`, which is
the live dwell timer, so a manual jump restarts it exactly when the countdown
restarts and the two can never disagree.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QVariantAnimation, QEasingCurve, pyqtSignal,
)
from PyQt6.QtGui import QPainter, QColor, QPen, QFontMetricsF

from app import brand
from app.rotation import rotation
from app.slides import Slide, coerce, FIGURE, ROSTER
from app.widgets.brand_widgets import paint_trace
from app.widgets.chassis import Chassis, SmoothRail


# Transition timings, in ms. The comp's numbers, kept as one table so a change
# is one edit rather than four.
_OUT_MS   = 260
_HOLD_MS  = 80
_IN_MS    = 420
_TRACE_MS = 900
_TOTAL_MS = _OUT_MS + _HOLD_MS + max(_IN_MS, _TRACE_MS)

_OUT_LIFT   = -22.0   # design px the outgoing slide rises
_IN_DROP    = 26.0    # design px the incoming slide rises from

_EASE_IN_CUBIC  = QEasingCurve(QEasingCurve.Type.InCubic)
_EASE_OUT_CUBIC = QEasingCurve(QEasingCurve.Type.OutCubic)
_EASE_OUT_EXPO  = QEasingCurve(QEasingCurve.Type.OutExpo)


class SlidePanel(Chassis):
    """
    The rotating stage. Connect `rotation.advance` → `next_slide()`.

    `slide_changed` fires on every move, however it was caused — the rotation
    timer or an operator jumping from the control screen — so a picker can stay
    in sync without polling.
    """

    slide_changed = pyqtSignal(int)

    def __init__(self, slides, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        self._slides: list[Slide] = coerce(slides)
        self._index = 0
        self._outgoing: Slide | None = None
        self._t = float(_TOTAL_MS)          # settled: no transition in flight

        # The rail advances about 2% a second, so a half-second tick is smooth
        # at fifteen feet and costs one repaint. (The chassis' own light-fall
        # drift is happy at any rate at or above its 2s.)
        self._drift.setInterval(500)

        # The rail is a child widget on its own 50ms tick. Repainting a
        # full-screen painted stage — a 140px headline, its layout, the plate —
        # fast enough for a rail to look smooth would be absurd, so the panel
        # keeps its slow tick and the rail keeps its own.
        self._rail = SmoothRail(self, rotation.progress)
        self._rail.hide()

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(_TOTAL_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(float(_TOTAL_MS))
        self._anim.valueChanged.connect(self._on_anim)
        self._anim.finished.connect(self._on_anim_done)

    # ── The rotation API (unchanged from the widget this replaces) ────────

    def next_slide(self):
        self.set_slide(self._index + 1)

    def previous_slide(self):
        self.set_slide(self._index - 1)

    def set_slide(self, index: int):
        """
        Jump to a slide. Wraps in both directions, so the control screen's
        prev/next need no bounds checking. Idempotent — re-selecting the
        current slide emits nothing, which is what stops the control screen and
        the presentation screen echoing each other through config.
        """
        count = len(self._slides)
        if count == 0:
            return
        index %= count
        if index == self._index:
            return
        self._outgoing = self._slides[self._index]
        self._index = index
        self._anim.stop()
        self._anim.start()
        self._place_rail()
        self.slide_changed.emit(index)

    def reset(self):
        self.set_slide(0)

    def titles(self) -> list[str]:
        return [s.title for s in self._slides]

    @property
    def count(self) -> int:
        return len(self._slides)

    @property
    def current_index(self) -> int:
        return self._index

    def apply_team(self, team):
        """Re-brand to the newly active team. The header reads config directly."""
        self.update()

    # ── Transition ────────────────────────────────────────────────────────

    def _on_anim(self, value):
        self._t = float(value)
        self.update()

    def _on_anim_done(self):
        self._outgoing = None
        self._t = float(_TOTAL_MS)
        self.update()

    def _incoming_phase(self) -> tuple[float, float]:
        """(opacity, y offset in design px) for the arriving slide."""
        t = self._t - _OUT_MS - _HOLD_MS
        if t <= 0:
            return 0.0, _IN_DROP
        k = _EASE_OUT_CUBIC.valueForProgress(min(1.0, t / _IN_MS))
        return k, _IN_DROP * (1.0 - k)

    def _outgoing_phase(self) -> tuple[float, float]:
        if self._outgoing is None or self._t >= _OUT_MS:
            return 0.0, 0.0
        k = _EASE_IN_CUBIC.valueForProgress(self._t / _OUT_MS)
        return 1.0 - k, _OUT_LIFT * k

    def _trace_progress(self) -> float:
        t = self._t - _OUT_MS - _HOLD_MS
        if t <= 0:
            return 0.0
        return _EASE_OUT_EXPO.valueForProgress(min(1.0, t / _TRACE_MS))

    # ── Header / footer ───────────────────────────────────────────────────

    @property
    def _screen_letter(self) -> str:
        return "B" if self.screen_id.endswith("_b") else "A"

    def header_right(self) -> list[tuple]:
        slide = self._slides[self._index] if self._slides else None
        if slide is not None and slide.kind == FIGURE:
            # A telemetry slide says so: the Pocket marks engineering content,
            # and the seal is this surface's one red.
            return [("pocket", brand.GRAPHITE),
                    ("mono", "TELEMETRY"),
                    ("seal", "FROM LOG", brand.RED)]
        return [("mono", f"SCREEN {self._screen_letter}"
                         "  /  STANDARD")]

    def _ledger_metrics(self):
        """
        (position text, label text, rail rect) for the footer ledger.

        Shared by the painter and `resizeEvent` so the rail child lands exactly
        where the two mono labels leave room for it — and so **geometry is
        never set from inside a paint event**, which is how a repaint loop
        starts.
        """
        c = self.content_rect()
        rect = self.footer_rect()
        position = f"{self._index + 1:02d} / {len(self._slides):02d}"
        label = f"ROTATION {self._screen_letter}"

        fm = QFontMetricsF(self.mono(20, 500, 0.12))
        gap = self.s(26)
        pos_w = self.s(112)
        label_w = fm.horizontalAdvance(label)
        rail_h = max(2.0, self.s(6))
        cy = rect.center().y()

        if self._screen_letter == "A":
            rail_x = c.x() + pos_w + gap
            rail_w = c.right() - label_w - gap - rail_x
        else:
            rail_x = c.x() + label_w + gap
            rail_w = c.right() - pos_w - gap - rail_x
        return position, label, QRectF(rail_x, cy - rail_h / 2,
                                       max(0.0, rail_w), rail_h)

    def _place_rail(self):
        if not self._slides:
            self._rail.hide()
            return
        _, _, r = self._ledger_metrics()
        if r.width() <= 0:
            self._rail.hide()
            return
        self._rail.setGeometry(int(r.x()), int(r.y()),
                               int(r.width()), int(r.height()))
        self._rail.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_rail()

    def paint_footer(self, p: QPainter, rect: QRectF):
        c = self.content_rect()
        rule_y = self.height() - self.m("stage_bottom")
        p.setPen(QPen(QColor(self.rule), self.m("rule")))
        p.drawLine(QPointF(c.x(), rule_y), QPointF(c.right(), rule_y))

        if not self._slides:
            return

        position, label, _ = self._ledger_metrics()
        f = self.mono(20, 500, 0.12)
        p.setFont(f)
        p.setPen(QColor(self.faint))
        pos_w = self.s(112)
        label_w = p.fontMetrics().horizontalAdvance(label)

        # A and B share the chassis and differ here: the rail sits left on A,
        # right on B, and the position mono trades places with the label.
        if self._screen_letter == "A":
            p.drawText(QRectF(c.x(), rect.y(), pos_w, rect.height()),
                       int(Qt.AlignmentFlag.AlignLeft |
                           Qt.AlignmentFlag.AlignVCenter), position)
            p.drawText(QRectF(c.right() - label_w, rect.y(), label_w,
                              rect.height()),
                       int(Qt.AlignmentFlag.AlignRight |
                           Qt.AlignmentFlag.AlignVCenter), label)
        else:
            p.drawText(QRectF(c.x(), rect.y(), label_w, rect.height()),
                       int(Qt.AlignmentFlag.AlignLeft |
                           Qt.AlignmentFlag.AlignVCenter), label)
            p.drawText(QRectF(c.right() - pos_w, rect.y(), pos_w,
                              rect.height()),
                       int(Qt.AlignmentFlag.AlignRight |
                           Qt.AlignmentFlag.AlignVCenter), position)

    # ── The stage ─────────────────────────────────────────────────────────

    def paint_stage(self, p: QPainter, rect: QRectF):
        if not self._slides:
            return

        out_alpha, out_dy = self._outgoing_phase()
        if out_alpha > 0 and self._outgoing is not None:
            p.save()
            p.setOpacity(out_alpha)
            p.translate(0, self.s(out_dy))
            self._paint_slide(p, rect, self._outgoing, trace=1.0)
            p.restore()

        in_alpha, in_dy = self._incoming_phase()
        if in_alpha > 0:
            p.save()
            p.setOpacity(in_alpha)
            p.translate(0, self.s(in_dy))
            self._paint_slide(p, rect, self._slides[self._index],
                              trace=self._trace_progress())
            p.restore()

    def _paint_slide(self, p: QPainter, rect: QRectF, slide: Slide,
                     trace: float):
        if slide.kind == FIGURE:
            self._paint_grid_lines(p)
            self._paint_figure(p, rect, slide)
        elif slide.kind == ROSTER:
            self._paint_roster(p, rect, slide)
        else:
            self._paint_statement(p, rect, slide, trace)

    # ── Statement ─────────────────────────────────────────────────────────

    def _paint_statement(self, p: QPainter, rect: QRectF, slide: Slide,
                         trace: float):
        x, y = rect.x(), rect.y()

        if slide.eyebrow:
            f = self.display(24, 600, 0.16)
            p.setFont(f)
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(x, y + p.fontMetrics().ascent()),
                       slide.eyebrow.upper())
            y += self.s(24)

        # The Trace is this archetype's one red, leading into the headline.
        trace_w = self.s(400)
        y += self.s(16)
        paint_trace(p, x, y, trace_w, brand.RED, trace)
        y += trace_w * 62 / 300 - self.s(12)

        y += self.s(12)
        y = self.draw_wrapped(p, x, y, min(self.s(1300), rect.width()),
                              slide.title, self.display(140, 700, -0.018),
                              self.ink, line_height=1.0)

        if slide.body:
            y += self.s(34)
            self.draw_wrapped(p, x, y, min(self.s(900), rect.width()),
                              slide.body, self.body(34), self.body_ink,
                              line_height=1.5)

    # ── Figure ────────────────────────────────────────────────────────────

    def _paint_grid_lines(self, p: QPainter):
        """
        The ledger's visible modular grid — structure as decoration, so a data
        fact never wears an authored slide's clothes. Four percent alpha: felt,
        not read.
        """
        c = self.content_rect()
        plate = self.plate_rect()
        p.setPen(QPen(QColor(243, 241, 240, 10), 1))
        for i in range(8):
            gx = c.x() + c.width() * i / 7.0
            p.drawLine(QPointF(gx, plate.top()), QPointF(gx, plate.bottom()))

    _FIGURE_PX = 260.0   # design size of the numeral
    _UNIT_PX = 62.0      # design size of its unit
    _FIGURE_MIN = 96.0   # below this it stops being the headline

    def _fit_figure(self, numeral: str, unit: str,
                    column_w: float) -> tuple[float, float]:
        """Design px for (numeral, unit) that fit `column_w`, in that ratio."""
        num_w = QFontMetricsF(
            self.display(self._FIGURE_PX, 700, -0.035)).horizontalAdvance(numeral)
        unit_w = (QFontMetricsF(
            self.display(self._UNIT_PX, 600)).horizontalAdvance(unit)
            + self.s(22)) if unit else 0.0
        needed = num_w + unit_w
        if needed <= column_w or needed <= 0:
            return self._FIGURE_PX, self._UNIT_PX
        k = column_w / needed
        return (max(self._FIGURE_MIN, self._FIGURE_PX * k),
                max(self._FIGURE_MIN * self._UNIT_PX / self._FIGURE_PX,
                    self._UNIT_PX * k))

    def _paint_figure(self, p: QPainter, rect: QRectF, slide: Slide):
        gap = self.s(76)
        left_w = (rect.width() - gap) * 1.15 / 2.15
        right_x = rect.x() + left_w + gap

        # ── Left: the numeral is the headline ──
        # 260px is the *maximum*, not the size. A real log yields figures from
        # "14.2" to "62,118,775", and a ten-glyph numeral set at 260 runs
        # straight through the rule and out of the plate — so the numeral is
        # fitted to its column and the unit shrinks with it, keeping their
        # relationship (260 : 62) intact at any width.
        numeral = slide.figure or slide.title
        num_px, unit_px = self._fit_figure(numeral, slide.unit, left_w)
        f_num = self.display(num_px, 700, -0.035)
        f_unit = self.display(unit_px, 600)
        p.setFont(f_num)
        num_fm = p.fontMetrics()
        block_h = self.s(24) + self.s(16) + num_fm.ascent()
        top = rect.center().y() - block_h / 2

        if slide.eyebrow:
            p.setFont(self.display(24, 600, 0.16))
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(rect.x(), top + p.fontMetrics().ascent()),
                       slide.eyebrow.upper())

        baseline = top + self.s(24) + self.s(16) + num_fm.ascent()
        p.setFont(f_num)
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(rect.x(), baseline), numeral)

        if slide.unit:
            p.setFont(f_unit)
            p.setPen(QColor(brand.GRAPHITE))
            p.drawText(QPointF(rect.x() + num_fm.horizontalAdvance(numeral)
                               + self.s(22), baseline), slide.unit)

        # ── Right: the sentence is the caption, behind a 2px rule ──
        p.setPen(QPen(QColor(self.rule), self.m("rule")))
        p.drawLine(QPointF(right_x, rect.y()), QPointF(right_x, rect.bottom()))

        text_x = right_x + self.s(58)
        text_w = rect.right() - text_x
        f_title = self.display(64, 700, -0.012)
        f_body = self.body(32)

        title_h = self.draw_wrapped(p, text_x, 0, text_w, slide.title, f_title,
                                    self.ink, 1.1, measure_only=True)
        body_h = (self.draw_wrapped(p, text_x, 0, text_w, slide.body, f_body,
                                    self.body_ink, 1.5, measure_only=True)
                  if slide.body else 0.0)
        total = title_h + (self.s(26) + body_h if slide.body else 0.0)
        y = rect.center().y() - total / 2

        y = self.draw_wrapped(p, text_x, y, text_w, slide.title, f_title,
                              self.ink, 1.1)
        if slide.body:
            self.draw_wrapped(p, text_x, y + self.s(26), text_w, slide.body,
                              f_body, self.body_ink, 1.5)

    # ── Roster ────────────────────────────────────────────────────────────

    def _paint_roster(self, p: QPainter, rect: QRectF, slide: Slide):
        y = rect.y()
        if slide.eyebrow:
            p.setFont(self.display(24, 600, 0.16))
            p.setPen(QColor(self.muted))
            p.drawText(QPointF(rect.x(), y + p.fontMetrics().ascent()),
                       slide.eyebrow.upper())
            y += self.s(24)

        y += self.s(14)
        head_w = rect.width() * 0.58
        bottom = self.draw_wrapped(p, rect.x(), y, head_w, slide.title,
                                   self.display(92, 700, -0.018), self.ink, 1.0)

        if slide.body:
            # The sentence sits right-flush against the headline's baseline —
            # two blocks meeting at the same rule rather than one column.
            body_w = min(self.s(520), rect.width() - head_w - self.s(40))
            f = self.body(30)
            h = self.draw_wrapped(p, 0, 0, body_w, slide.body, f,
                                  self.body_ink, 1.45, measure_only=True)
            self.draw_wrapped(p, rect.right() - body_w, bottom - h, body_w,
                              slide.body, f, self.body_ink, 1.45,
                              align="right")

        # The grid of marks. Zero red on this surface: a sponsor mark brings
        # its own colour and would be the second red thing.
        grid_top = bottom + self.s(44)
        cells = list(slide.items) or ["SPONSOR MARK"] * 6
        cols = 3
        rows = max(1, (len(cells) + cols - 1) // cols)
        gap = self.s(26)
        cw = (rect.width() - gap * (cols - 1)) / cols
        ch = (rect.bottom() - grid_top - gap * (rows - 1)) / rows
        if ch <= 0:
            return

        r = self.s(12)
        f = self.mono(22, 500, 0.14)
        for i, text in enumerate(cells):
            col, row = i % cols, i // cols
            cell = QRectF(rect.x() + col * (cw + gap),
                          grid_top + row * (ch + gap), cw, ch)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(brand.N50))
            p.drawRoundedRect(cell, r, r)
            p.setFont(f)
            p.setPen(QColor(brand.N400))
            p.drawText(cell, int(Qt.AlignmentFlag.AlignCenter), text)
