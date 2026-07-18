"""
JudgesSlideManager — loads images from assets/judges_slides/ and tracks the
current slide index. Both JudgesOverlay instances and the control-screen
picker connect to its signals.

Students: drop numbered PNG/JPG files into assets/judges_slides/ and click
Reload on the control screen. Files are sorted alphabetically, so naming
them 01_intro.png, 02_robot.png etc. controls the order.
"""

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap

from app.lazy_proxy import LazyProxy

SLIDES_DIR = Path(__file__).parent.parent / "assets" / "judges_slides"
_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}


class JudgesSlideManager(QObject):

    # Fired when the displayed slide changes (index, full-res pixmap)
    slide_changed = pyqtSignal(int, QPixmap)

    # Fired after the file list is rescanned (add/remove files)
    slides_reloaded = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._paths: list[Path] = []
        self._index: int = 0
        SLIDES_DIR.mkdir(parents=True, exist_ok=True)
        self._scan()

    # ── File scanning ─────────────────────────────────────────────────────

    def _scan(self):
        self._paths = sorted(
            p for p in SLIDES_DIR.iterdir() if p.suffix.lower() in _EXTS
        ) if SLIDES_DIR.exists() else []
        self._index = min(self._index, max(0, len(self._paths) - 1))
        self.slides_reloaded.emit()

    def reload(self):
        """Re-scan the folder; emits slide_changed to refresh all overlays."""
        self._scan()
        if self._paths:
            self.slide_changed.emit(self._index, self._load(self._index))

    # ── Navigation ────────────────────────────────────────────────────────

    def go_to(self, index: int):
        if not self._paths:
            return
        self._index = index % len(self._paths)
        self.slide_changed.emit(self._index, self._load(self._index))

    def next(self):
        self.go_to(self._index + 1)

    def prev(self):
        self.go_to(self._index - 1)

    # ── Accessors ─────────────────────────────────────────────────────────

    @property
    def paths(self) -> list[Path]:
        return list(self._paths)

    @property
    def count(self) -> int:
        return len(self._paths)

    @property
    def index(self) -> int:
        return self._index

    def current_pixmap(self) -> "QPixmap | None":
        if not self._paths:
            return None
        return self._load(self._index)

    def _load(self, index: int) -> QPixmap:
        return QPixmap(str(self._paths[index]))


judges_slides: JudgesSlideManager = LazyProxy(
    "judges_slides", "init_judges_slides"
)  # type: ignore[assignment]


def init_judges_slides() -> JudgesSlideManager:
    """Call once in main(), after init_config()."""
    real = JudgesSlideManager()
    judges_slides._install(real)
    return real
