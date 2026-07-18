"""
Judges overlay — displays the current slide from JudgesSlideManager.

The header bar and image area are wired up. When no slides are loaded the
overlay shows a drop-in hint pointing students to the slides folder.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QSizePolicy
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

from app import brand
from app.config import config
from app.judges_slides import judges_slides


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

        header_layout = QVBoxLayout(self._header)
        header_layout.setContentsMargins(32, 0, 32, 0)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._header_label = QLabel()
        self._header_label.setObjectName("section_header")
        header_layout.addWidget(self._header_label)

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
        accent = team.primary_color

        team_label = (f"{team.name} {team.number}" if team.name
                      else f"Team {team.number}").upper()
        self._header_label.setText(f"{team_label}  ·  JUDGING")

        bg, header_bg, hint_color = pal["bg"], pal["surface"], pal["muted"]

        self.setStyleSheet(f"""
            JudgesOverlay {{
                background-color: {bg};
            }}
            QFrame#judges_header {{
                background-color: {header_bg};
                border: none;
                border-bottom: 2px solid {accent};
            }}
            QLabel#section_header {{
                color: {accent};
                font-family: "{brand.FONT_DISPLAY}";
                font-size: 13px;
                font-weight: 600;
                letter-spacing: 2px;
            }}
            _ImageLabel {{
                background-color: {bg};
                color: {hint_color};
                font-family: "{brand.FONT_BODY}";
                font-size: 15px;
            }}
        """)
