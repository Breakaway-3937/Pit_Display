"""
Judges overlay — displays the current slide from JudgesSlideManager.

**The artwork is full-bleed, deliberately.** Every other surface here sits on
the shared plate, but a judging slide is the team's own finished graphic and
boxing it inside a second frame would put two containers around one image. What
follows the system instead is the *chrome*: the same header band, the same
wordmark-as-type, the same mono screen label, the same 2px rule.

It carries **no red**. The band used to set the team accent as a 2px underline
*and* as the label's colour — red letterforms on a dark ground are 2.8:1 and
forbidden, and the slide below is the thing judges are meant to be looking at.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap

from app import brand
from app.config import config
from app.judges_slides import judges_slides
from app.widgets.brand_widgets import mono_font


# ── Scaled image label ────────────────────────────────────────────────────────

class _ImageLabel(QLabel):
    """QLabel that keeps its pixmap scaled to the available space."""

    _HINT = "No slides loaded.\n\nDrop images into\nassets/judges_slides/\nthen click Reload\non the control screen."

    def __init__(self):
        super().__init__(self._HINT)
        self._source: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)

    def set_source(self, pm: QPixmap | None):
        self._source = pm
        self._rescale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self):
        if self._source and not self._source.isNull():
            scaled = self._source.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            super().setPixmap(scaled)
            self.setText("")
        else:
            super().clear()
            self.setText(self._HINT)


# ── Overlay ───────────────────────────────────────────────────────────────────

class JudgesOverlay(QWidget):

    def __init__(self, screen_id: str = "", parent=None):
        super().__init__(parent)
        self._screen_id = screen_id
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()
        config.team_changed.connect(self._refresh_style)
        config.screen_setting_changed.connect(self._on_setting_changed)
        judges_slides.slide_changed.connect(self._on_slide_changed)
        judges_slides.slides_reloaded.connect(self._on_slides_reloaded)
        self._refresh_style()
        # Show whatever slide is already loaded
        self._image_lbl.set_source(judges_slides.current_pixmap())

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────────────
        self._header = QFrame()
        self._header.setFixedHeight(64)
        self._header.setObjectName("judges_header")

        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(40, 0, 40, 0)

        # Identity as type, exactly as the chassis header sets it.
        self._wordmark = QLabel()
        self._wordmark.setTextFormat(Qt.TextFormat.RichText)
        wf = QFont(brand.FONT_DISPLAY)
        wf.setPixelSize(22)
        wf.setWeight(QFont.Weight.Bold)
        wf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102)
        self._wordmark.setFont(wf)
        header_layout.addWidget(self._wordmark,
                                alignment=Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch(1)

        self._header_label = QLabel()
        self._header_label.setFont(mono_font(15))
        header_layout.addWidget(self._header_label,
                                alignment=Qt.AlignmentFlag.AlignVCenter)

        root.addWidget(self._header)

        # ── Slide image area ──────────────────────────────────────────────
        self._image_lbl = _ImageLabel()
        root.addWidget(self._image_lbl, stretch=1)

    # ── Slide updates ─────────────────────────────────────────────────────

    def _on_slide_changed(self, _index: int, pixmap: QPixmap):
        self._image_lbl.set_source(pixmap)

    def _on_slides_reloaded(self):
        self._image_lbl.set_source(judges_slides.current_pixmap())

    # ── Theming ───────────────────────────────────────────────────────────

    def _on_setting_changed(self, screen: str, key: str, _value):
        if screen == self._screen_id and key == "theme":
            self._refresh_style()

    def _refresh_style(self, *_):
        theme = config.screen_theme(self._screen_id) if self._screen_id else "dark"
        pal = brand.palette(theme)
        team = config.active_team
        ink = pal["title"]
        faint = pal["faint"]
        self._wordmark.setText(
            f'<span style="color:{ink}">{(team.name or "Team").upper()}</span>'
            f'<span style="color:{faint}"> {team.number}</span>')

        letter = "B" if self._screen_id.endswith("_b") else "A"
        self._header_label.setText(f"SCREEN {letter}  /  JUDGING")

        bg, header_bg, hint_color = pal["bg"], pal["surface"], pal["muted"]

        self.setStyleSheet(f"""
            JudgesOverlay {{
                background-color: {bg};
            }}
            QFrame#judges_header {{
                background-color: {header_bg};
                border: none;
                border-bottom: 2px solid {pal["line"]};
            }}
            _ImageLabel {{
                background-color: {bg};
                color: {hint_color};
                font-family: "{brand.FONT_BODY}";
                font-size: 15px;
            }}
        """)
        self._header_label.setStyleSheet(
            f"color: {faint}; background: transparent;")
