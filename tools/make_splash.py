"""
Render `packaging/splash.png`, the launcher splash, from the boot screen's
own drawing code.

    uv run tools/make_splash.py

PyInstaller's launcher shows this image before Python starts (Windows builds,
`packaging/pit_display.spec`), then `main.py` swaps in the live Qt boot screen
at the same size and position. Rendering one from the other is what makes the
hand-off read as a single screen: change `app/widgets/boot_splash.py`, rerun
this, commit the PNG. The status line is left blank here because the launcher
draws its own text there (`LAUNCHER_TEXT_POS` in the spec).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.console import use_utf8  # noqa: E402

use_utf8()

from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    import main as app_main
    app_main._load_fonts(app)
    from app.widgets import boot_splash as b

    image = QImage(b.W, b.H, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    p = QPainter(image)
    b.paint(p, "", "", 0.0, "", launcher=True)
    p.end()
    out = ROOT / "packaging" / "splash.png"
    if not image.save(str(out)):
        print(f"could not write {out}")
        return 1
    print(f"wrote {out} ({b.W}×{b.H})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
