"""
Lunch — the holding card.

The pit is unattended. Say so warmly, keep the brand on screen, and be the
calmest surface in the room: this is the one that runs for forty minutes with
nobody watching it, so it is one enormous line of type and nothing competing.

**On the same chassis as everything else.** It used to be a bespoke
`paintEvent` with a wordmark plate, a red pill divider and red eyebrow type —
three things the brand system now rules out:

- **The wordmark artwork is out.** Identity is carried by the type in the
  header band, which frees the whole red budget for functional shapes and
  removes the white media plate that was breaking the dark surface.
- **Red on carbon is 2.8:1 and forbidden for type.** The eyebrow goes muted;
  the one red thing is the **Trace**, a filled shape leading into the headline.
- The plate, the header and the footer ledger come from `Chassis`, so an
  operator flipping between Standard and Lunch sees the same room.

**The headline auto-fits.** It has to genuinely fill a 55" panel, and the
message is short and known, so it is measured against the stage rather than set
at a size that happens to work on one machine.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter

from app import brand
from app.config import config
from app.widgets.brand_widgets import paint_trace
from app.widgets.chassis import Chassis

HEADLINE = "We'll Be Right Back"
SUBLINE = ("Our team is on a lunch break and will return shortly. "
           "Thank you for stopping by our pit.")

# Design sizes at 1920×1080. The headline is a ceiling, not a size — `_fit()`
# takes it down until the line fits the stage.
_HEAD_MAX = 200
_HEAD_MIN = 48
_EYEBROW = 24
_BODY = 34
_TRACE_W = 400


class LunchOverlay(Chassis):
    """The unattended-pit card, on the shared chassis."""

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(screen_id=screen_id, parent=parent)
        config.team_changed.connect(lambda *_: self.update())

    # ── Chassis hooks ─────────────────────────────────────────────────────

    @property
    def _screen_letter(self) -> str:
        return "B" if self.screen_id.endswith("_b") else "A"

    def header_right(self) -> list[tuple]:
        return [("mono", f"SCREEN {self._screen_letter}  /  LUNCH")]

    def footer_items(self) -> tuple[str, str]:
        # The eyebrow already says "back shortly"; the ledger says where you
        # are, which is the other thing a visitor standing here wants.
        team = config.active_team
        where = (team.location or "").upper()
        return "", where or "THANKS FOR STOPPING BY"

    # ── The stage ─────────────────────────────────────────────────────────

    def _fit(self, p: QPainter, text: str, max_w: float,
             max_px: float) -> QFont:
        """The largest display size at which `text` still fits on one line."""
        px = max_px
        while px > _HEAD_MIN:
            f = self.display(px, 700, -0.018)
            if QFontMetricsF(f).horizontalAdvance(text) <= max_w:
                return f
            px *= 0.94
        return self.display(_HEAD_MIN, 700, -0.018)

    def paint_stage(self, p: QPainter, rect: QRectF):
        eyebrow = "Back shortly"
        f_eye = self.display(_EYEBROW, 600, 0.16)
        f_head = self._fit(p, HEADLINE, rect.width(), _HEAD_MAX)
        f_body = self.body(_BODY)

        trace_w = min(self.s(_TRACE_W), rect.width() * 0.35)
        trace_h = trace_w * 62 / 300

        eye_h = QFontMetricsF(f_eye).height()
        head_h = QFontMetricsF(f_head).height()
        body_h = self.draw_wrapped(p, rect.x(), 0, min(self.s(1100),
                                                       rect.width()),
                                   SUBLINE, f_body, self.body_ink, 1.5,
                                   measure_only=True)

        gap_a, gap_b, gap_c = self.s(16), self.s(12), self.s(34)
        total = eye_h + gap_a + trace_h + gap_b + head_h + gap_c + body_h
        # Centred on the stage: the card is looked at, not read through, so it
        # sits in the middle of its room rather than hanging off the top rule.
        y = rect.y() + max(0.0, (rect.height() - total) / 2)

        p.setFont(f_eye)
        p.setPen(QColor(self.muted))
        p.drawText(QPointF(rect.x(), y + QFontMetricsF(f_eye).ascent()),
                   eyebrow.upper())
        y += eye_h + gap_a

        # The one red thing on the surface, and it is a shape, not type.
        paint_trace(p, rect.x(), y, trace_w, brand.RED)
        y += trace_h + gap_b

        p.setFont(f_head)
        p.setPen(QColor(self.ink))
        p.drawText(QPointF(rect.x(), y + QFontMetricsF(f_head).ascent()),
                   HEADLINE)
        y += head_h + gap_c

        self.draw_wrapped(p, rect.x(), y, min(self.s(1100), rect.width()),
                          SUBLINE, f_body, self.body_ink, 1.5)

    # ── Kept for the presentation screen's mode switch ────────────────────

    def apply_fonts(self):
        """Nothing to cache any more; the stage measures itself every paint."""
        self.update()

    def reload_logo(self):
        """The wordmark artwork is no longer used — see the module docstring."""
        self.update()
