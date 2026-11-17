"""
Design files we both work on: the app's real screens as SVG, edited in
Illustrator, read back as a list of changes. See design/README.md.

    uv run tools/design.py list                      every screen it can draw
    uv run tools/design.py export overhead:robot     → design/screens/overhead-robot.svg (+ .png)
    uv run tools/design.py export all                every screen (never over your edits)
    uv run tools/design.py diff overhead-robot       what your edit changed against the code
    uv run tools/design.py lint design/inbox/x.svg   brand check: colours, fonts, the red budget
    uv run tools/design.py shot control:analysis     a PNG only, into design/out/
    uv run tools/design.py watch overhead:next_match        a window that redraws when code is saved
    uv run tools/design.py check                     every screen draws (exit 0/1, for validate)

**The loop.** `export` paints a screen from the code into an SVG: the same
painter that draws it on the pit, so text stays text (font, size, weight,
colour), shapes stay shapes, positions are design pixels. You open it in
Illustrator, change it, and save over the same file. `diff` paints the code
again and compares: text moved, resized, recoloured, reworded, added or
removed; colours brought in or dropped; where on the screen pixels changed
(a picture with the regions boxed); and a brand check of your file. That
list is the change request. When the code matches, `diff` says so, and
`export --force` makes the code's version the new starting point.

**Layered vectors.** Every widget is painted on its own and the pieces nest
as the widgets do, each group named (Illustrator's Layers panel): Screen A /
Screen B, then Ground, Lightfall, Header, Stage, Footer, then every label,
button and panel by name, and inside them each text ("Text Bridge
Brownout") and shape ("Shape #BA141A"). The chassis's cached background is
painted again as shapes. What stays a picture: logo artwork and anything
the app itself draws from an image.

**One master for the overhead frame.** Every overhead face sits in the same
chassis (ground, plate, header band, footer ledger), so its vectors live
once, in `overhead-master.svg`. Every other overhead file holds only that
face's own content, over a linked picture of its frame
(`refs/<name>.frame-a.png`, not editable, there for context). Rotation stops aren't files of their own:
**the Slides** (`app/deck.py`) are one Description layout and a few Detail
layouts with other words, so one file for the Description and one per Detail.

**Never overwrites your edit.** `export` records each file's hash
(`design/screens/.exports.json`) and refuses to write over a file that has
changed since, unless `--force`.

Targets, as `<surface>:<what>`:
  overhead:master             the frame every overhead face shares (A | B)
  overhead:<set>              **the overhead screens, always as their matched
                              set** (`app/overhead.py`): A on the left, B on
                              the right, one artboard. Sets: next_match, robot,
                              checklist, facts, analysis, stats, fun, datasets
  overhead:slide:description  every Slide's Description (one layout, here once)
  overhead:slide:<detail>     each Detail layout: quality, bk_seasons, dataset,
                              figures, record, fact (the other half a picture)
  overhead:lunch / :judges    the modes that take over both screens
  control:<panel>             the control screen with that panel open
                              (presentation_a, leds, music, network, analysis…)
  project:board / project:cad the pit-front portrait panel
Options: --theme dark|light, --mode standard|judges|lunch, --size WxH.

Every render boots a fresh install in `.validation/design/` with the test
robot log, a Nexus event with our matches and a few facts, as team 3937:
**never this Mac's own data.** A web view (the CAD model) paints blank in
an SVG; that's Chromium, not the screen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

DESIGN = ROOT / "design"
SCREENS = DESIGN / "screens"
OUT = DESIGN / "out"
EXPORTS = SCREENS / ".exports.json"
SCRATCH = ROOT / ".validation" / "design"
FIXTURE = ROOT / "TEST_LOGS" / "fixture_faults_2026-07-29.pitlog.zst"

OVERHEAD_W, OVERHEAD_H = 1920, 1080
GAP = 120               # between A and B on the overhead artboard: the pit's two screens
SIZES = {"overhead": (OVERHEAD_W * 2 + GAP, OVERHEAD_H), "project": (1080, 1920),
         "control": (1600, 1000)}
SETTLE_S = 1.6          # the slide transition (760 ms) and the Trace (900 ms) finish
MOVED_PX = 4            # a text that moved less than this hasn't moved
SIZE_PX = 0.5
CELL = 40               # the changed-regions grid, design pixels
CELL_CHANGED = 0.04     # share of a cell's sampled pixels that differ
PIXEL_DIFF = 24         # per-channel difference that counts as changed (anti-aliasing is less)
NEAR_MISS = 18          # an off-palette colour this close to a token is that token, drifted
FONTS = {"chakra petch", "roboto", "jetbrains mono"}
RED_FAMILY = {"#BA141A", "#A21117", "#8E1519"}


def is_red(c: str | None) -> bool:
    """The brand's reds, and anything drifted from them (an export's #BB151A)."""
    if not c:
        return False
    return c in RED_FAMILY or min(_dist(c, r) for r in RED_FAMILY) <= NEAR_MISS


# ── Targets ──────────────────────────────────────────────────────────────────

@dataclass
class Target:
    surface: str
    what: str
    theme: str = "dark"
    mode: str = "standard"
    size: tuple[int, int] = (0, 0)
    fixed: bool = False      # --size given: keep it, even for a control panel taller than it

    @property
    def name(self) -> str:
        n = f"{self.surface}-{self.what.replace(':', '-')}"
        if self.theme != "dark":
            n += f"__{self.theme}"
        if self.mode != "standard" and self.what != self.mode:
            n += f"__{self.mode}"
        return n

    def spec(self) -> str:
        return f"{self.surface}:{self.what}"


def parse_target(text: str, theme: str = "dark", mode: str = "standard",
                 size: str | None = None) -> Target:
    """`overhead:robot`, or a file name `overhead-robot__light` (its options win)."""
    text = Path(text).stem if text.endswith(".svg") else text
    if ":" not in text:                                  # a file name
        base, *opts = text.split("__")
        surface, _, what = base.partition("-")
        if what.startswith("slide-"):
            what = "slide:" + what[len("slide-"):]
        for o in opts:
            if o in ("dark", "light"):
                theme = o
            elif o in ("standard", "judges", "lunch"):
                mode = o
        text = f"{surface}:{what}"
    surface, _, what = text.partition(":")
    if surface not in SIZES:
        raise SystemExit(f"unknown surface {surface!r}: one of {', '.join(SIZES)}")
    if surface == "overhead" and what in ("judges", "lunch"):
        mode = what                       # a mode takes both screens: it's its own file
    w, h = SIZES[surface]
    if size and surface != "overhead":
        w, h = (int(v) for v in size.lower().split("x"))
    return Target(surface, what or "board", theme, mode, (w, h), fixed=bool(size))


def all_targets(slide_kinds: list[str] | None = None) -> list[str]:
    """Every design file, each once. The overhead screens: one master (the
    frame every face shares), each matched set, one file per slide template
    (not per rotation stop: the stops are those templates with other words),
    and the two modes. Slide templates are read off a booted install."""
    from app.overhead import SETS
    from app.windows.control_screen import SYSTEMS
    out = ["overhead:master"]
    out += [f"overhead:slide:{k}" for k in (slide_kinds or ["description"])]
    out += [f"overhead:{s}" for s in SETS if s != "rotation"]
    out += ["overhead:lunch", "overhead:judges"]
    out += [f"control:{s}" for s in ["presentation_a", "presentation_b", "project", *SYSTEMS]]
    out += ["project:board"]
    return out


def _detail_kind(entry) -> str:
    """A Detail's layout: its face, or for a card what kind of card."""
    if entry.face != "fact_card":
        return entry.face
    return {"card": "figures", "record": "record", "fact": "fact"}.get(
        entry.item.partition(":")[0], "card")


def _slide_stops() -> dict[str, int]:
    """The Slides' templates → the first stop that shows each: `description`
    (every Slide's Description is one layout), then one per Detail layout.
    Fifty Slides are content, not fifty files."""
    from app import program
    out: dict[str, int] = {}
    for i, stop in enumerate(program.stops()):
        detail = stop.a if stop.a.face else stop.b if stop.b.face else None
        if detail is None:
            continue
        out.setdefault("description", i)
        out.setdefault(_detail_kind(detail), i)
    return out


# ── Rendering (in a fresh process: `design.py _render …`) ────────────────────

_APP = None


def _boot() -> None:
    """A fresh install with test data, every service the screens read."""
    global _APP
    shutil.rmtree(SCRATCH, ignore_errors=True)
    (SCRATCH / "data").mkdir(parents=True)
    os.environ.update(PIT_DISPLAY_DATA=str(SCRATCH), QT_QPA_PLATFORM="offscreen",
                      PIT_LEDS_FAKE="1", PIT_NEXUS_FAKE="1", PIT_NEXUS_QUIET="1",
                      PIT_SYNC_QUIET="1", PIT_AI_QUIET="1", PIT_DIAG_QUIET="1",
                      PIT_CAD_PORT="0", PIT_BATTERIES_FAKE="1")
    # Offscreen there is no GPU: Chromium logs a lost context a thousand times.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --log-level=3")
    from PyQt6 import QtWebEngineWidgets  # noqa: F401  (before QApplication)
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication(["design"])
    from main import _load_fonts
    _load_fonts(_APP)

    import app.db.migrations  # noqa: F401
    from app.config import config, init_config
    from app.db import init_db
    init_config()
    db = init_db()
    from app.admin import init_admin
    from app.batteries import init_batteries
    from app.cad_assets import init_cad_assets
    from app.checklist import init_checklist
    from app.judges_slides import init_judges_slides
    from app.leds import init_leds
    from app.music import init_music
    from app.nexus import init_nexus, nexus
    from app.nexus.alerts import init_alerts
    from app.rotation import init_rotation
    from app.update import init_update
    for fn in (init_update, init_nexus, init_admin, init_checklist, init_rotation,
               init_judges_slides, init_cad_assets, init_leds, init_batteries,
               init_music, init_alerts):
        fn()
    from app.webcast import init_webcast
    init_webcast()
    from app.db.sync.service import init_sync
    init_sync()
    from app.ai.service import init_analysis
    init_analysis()
    from app.diagnostics.service import init_diagnostics
    init_diagnostics()
    from app.theme import dark_qss
    _APP.setStyleSheet(dark_qss())

    # A robot log, an event with our matches, facts from home.
    from app.db.sync import bundle
    (SCRATCH / "bundle").mkdir()
    bundle.import_bundle(db.path, FIXTURE, SCRATCH / "bundle")
    config.set_team(3937)
    from app.nexus import api
    snaps = api.load_fixtures()["event_status"]
    snap = api.EventStatus.from_json(json.loads(json.dumps(snaps[-1]).replace('"700"', '"3937"')))
    nexus._event_key = snap.event_key or "2025inind"
    nexus._status = snap
    for i, (cat, text) in enumerate([
            ("3937", "Breakaway has won 53 awards and 10 blue banners since 2012."),
            ("3937", "Breakaway has brought home an award 13 seasons in a row (since 2014)."),
            ("arkansas", "Arkansas has had 81 FRC teams; 8 played in 2026."),
            ("arkansas", "An Arkansas team has won the Arkansas Regional 11 of 13 years.")]):
        db.execute("INSERT INTO tba_fact (uid, category, text, sort, data) "
                   "VALUES (?, ?, ?, ?, '{}')", (f"f{i}", cat, text, i))
    # Home's datasets (the same fixture sync_check and program_check use),
    # cleared, so every kind of Slide has something to show.
    sys.path.insert(0, str(ROOT / "tools"))
    from sync_check import HOME_DATASETS
    from app import dataset_settings
    for row in HOME_DATASETS:
        db.execute("INSERT INTO home_dataset (uid, title, category, sort, data) VALUES (?, ?, ?, ?, ?)",
                   (row["uid"], row["data"]["title"], row["data"]["category"],
                    row["data"]["sort"], json.dumps(row["data"])))
    dataset_settings.save(enabled=[r["uid"] for r in HOME_DATASETS])
    from app.rotation import rotation
    rotation.refresh()


def _settle(seconds: float = SETTLE_S) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.01)


def _build(t: Target):
    from app.config import config
    from app.theme import apply_theme
    # A screen follows `mode_changed`; set the mode once it's built.
    if t.surface == "overhead":
        return _build_overhead(t)
    if t.surface == "control":
        from app.windows.control_screen import ControlScreen
        w = ControlScreen()
        if t.what not in w._settings_panels:
            raise SystemExit(f"no panel {t.what!r}: one of {', '.join(w._settings_panels)}")
        w._select_screen(t.what)
        if t.theme == "light":
            apply_theme(w, "light")
    else:
        from app.windows.project_screen import ProjectScreen
        w = ProjectScreen()
        config.set("project", "content", t.what)
        config.set("project", "theme", t.theme)
    w.resize(*t.size)
    w.show()
    config.set_mode(t.mode)
    _settle()
    if not t.fixed:
        # The body scrolls; a design file wants the whole page. Grow the
        # window until nothing is left below the fold.
        from PyQt6.QtWidgets import QScrollArea
        for _ in range(4):           # a page can re-wrap as it grows
            more = max((a.verticalScrollBar().maximum() for a in w.findChildren(QScrollArea)
                        if a.isVisibleTo(w)), default=0)
            if more <= 0:
                break
            w.resize(w.width(), w.height() + more)
            _settle(0.4)
        t.size = (w.width(), w.height())
    return w


class _Pair:
    """Both overhead windows, drawn as one: A, the gap, B."""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def grab(self):
        from PyQt6.QtGui import QColor, QPainter, QPixmap
        pix = QPixmap(*SIZES["overhead"])
        pix.fill(QColor("#808080"))
        p = QPainter(pix)
        p.drawPixmap(0, 0, self.a.grab())
        p.drawPixmap(OVERHEAD_W + GAP, 0, self.b.grab())
        p.end()
        return pix

    def close(self):
        self.a.close()
        self.b.close()

    def deleteLater(self):
        self.a.deleteLater()
        self.b.deleteLater()


def _build_overhead(t: Target) -> _Pair:
    """The set goes on both screens the way the control screen puts it there."""
    from app import overhead
    from app.config import config
    from app.rotation import rotation
    from app.windows.presentation_a import PresentationScreenA
    from app.windows.presentation_b import PresentationScreenB
    name, _, kind = t.what.partition(":")
    slides = _slide_stops()
    if name in ("master", "slide", "lunch", "judges"):
        overhead.choose("rotation")
        if name == "slide" and kind not in slides:
            raise SystemExit(f"no slide template {kind!r} in the program: {', '.join(slides) or 'none'}")
        stop = slides.get(kind) if name == "slide" else next(iter(slides.values()), 0)
    elif name in overhead.SETS and name != "rotation":
        overhead.choose(name)
    else:
        raise SystemExit(f"no overhead file {name!r}: master, slide:<template>, lunch, judges, or a "
                         f"set ({', '.join(s for s in overhead.SETS if s != 'rotation')})")
    a, b = PresentationScreenA(), PresentationScreenB()
    for screen in overhead.SIDES:
        config.set(screen, "theme", t.theme)
    for w in (a, b):
        w.resize(OVERHEAD_W, OVERHEAD_H)
        w.show()
    if name in ("master", "slide", "lunch", "judges"):
        # The screens follow the position as it changes: move once they exist.
        rotation._set(stop + 1)
        rotation._set(stop)
    config.set_mode(t.mode)
    _settle()
    return _Pair(a, b)


# ── Layered SVG ──────────────────────────────────────────────────────────────
#
# Qt's SVG painter writes one flat run of unnamed groups: nothing to select
# in Illustrator. So each widget is painted on its own (its children left
# out) and the pieces nest as the widgets do, every group named (`id` is
# what Illustrator's Layers panel shows). A chassis is split into its own
# layers: ground, lightfall, header, stage, footer. Its ground, cached as a
# picture in the app, is painted again as shapes.

_EMPTY_G = re.compile(r"<g\b[^>]*>\s*</g>")
_DRAWS = re.compile(r"<(path|rect|text|image|polyline|polygon|circle|ellipse|line)\b")
_SHAPE_G = re.compile(r"<g\b(?![^>]*\bid=)([^>]*)>(\s*<(?:path|rect|polyline|polygon|circle|ellipse|line)\b)")
_TEXT_G = re.compile(r"<g\b(?![^>]*\bid=)([^>]*)>(\s*<text\b[^>]*>([^<]*)</text>)")
_IMAGE_G = re.compile(r"<g\b(?![^>]*\bid=)([^>]*)>(\s*<image\b)")


class _Svg:
    """One layered document: named groups from painted fragments."""

    def __init__(self):
        self.n = 0
        self.ids: Counter = Counter()
        self.reference = ""     # the frame picture under a face file's content

    def ident(self, label: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")[:60] or "Layer"
        if not base[0].isalpha():
            base = "L_" + base
        self.ids[base] += 1
        return base if self.ids[base] == 1 else f"{base}_{self.ids[base]}"

    def group(self, label: str, inner: str, transform: str | None = None,
              layer: bool = False) -> str:
        """`layer`: a top-level Illustrator layer (its own `i:layer`), not a group."""
        if not inner.strip():
            return ""
        from xml.sax.saxutils import quoteattr
        t = f' transform="{transform}"' if transform else ""
        lay = ' i:layer="yes"' if layer else ""
        return f'<g id="{self.ident(label)}" data-name={quoteattr(label)}{lay}{t}>\n{inner}</g>\n'

    def fragment(self, size: tuple[int, int], paint) -> str:
        from PyQt6.QtCore import QBuffer, QIODevice, QRect, QSize
        from PyQt6.QtGui import QPainter
        from PyQt6.QtSvg import QSvgGenerator
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        gen = QSvgGenerator()
        gen.setOutputDevice(buf)
        gen.setSize(QSize(*size))
        gen.setViewBox(QRect(0, 0, *size))
        p = QPainter(gen)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        paint(p)
        p.end()
        return self.clean(bytes(buf.data()).decode("utf-8"))

    def clean(self, xml: str) -> str:
        import html
        body = xml[xml.index(">", xml.index("<svg")) + 1: xml.rindex("</svg>")]
        body = re.sub(r"<title>.*?</title>|<desc>.*?</desc>", "", body, flags=re.S)
        self.n += 1
        pre = f"f{self.n}_"
        body = re.sub(r'\bid="([^"]+)"', lambda m: f'id="{pre}{m.group(1)}"', body)
        body = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{pre}{m.group(1)})", body)
        body = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{pre}{m.group(1)}"', body)
        # Qt writes the whole fallback stack ("JetBrains Mono,Menlo,…");
        # Illustrator wants one family it has installed.
        body = re.sub(r'font-family="([^",]+),[^"]*"', r'font-family="\1"', body)
        prev = None
        while prev != body:
            prev, body = body, _EMPTY_G.sub("", body)
        if not _DRAWS.search(body):
            return ""
        body = _TEXT_G.sub(lambda m: f'<g id="{self.ident("Text " + html.unescape(m.group(3)).strip()[:40])}"'
                                     f'{m.group(1)}>{m.group(2)}', body)

        def shape(m):
            fill = re.search(r'\bfill="(#[0-9a-fA-F]{6})"', m.group(1)) or \
                re.search(r'\bstroke="(#[0-9a-fA-F]{6})"', m.group(1))
            return f'<g id="{self.ident("Shape " + (fill.group(1).upper() if fill else ""))}"{m.group(1)}>{m.group(2)}'
        body = _SHAPE_G.sub(shape, body)
        body = _IMAGE_G.sub(lambda m: f'<g id="{self.ident("Picture")}"{m.group(1)}>{m.group(2)}', body)
        return body


def _label(w) -> str:
    from PyQt6.QtWidgets import QAbstractButton, QLabel, QLineEdit
    name = w.objectName()
    name = name if name and not name.startswith("qt_") else type(w).__name__
    text = ""
    if isinstance(w, (QLabel, QAbstractButton)):
        text = w.text()
    elif isinstance(w, QLineEdit):
        text = w.text() or w.placeholderText()
    text = " ".join(re.sub(r"<[^>]+>", " ", text or "").split())
    return f"{name} · {text[:40]}" if text else name


def _vector_ground(svg: _Svg, ch) -> str:
    """The chassis's ground and plate as shapes: its picture cache, painted
    into an SVG instead (the module's QPixmap swapped for one that is)."""
    from PyQt6.QtCore import QBuffer, QIODevice, QRect, QSize
    from PyQt6.QtGui import QColor
    from PyQt6.QtSvg import QSvgGenerator
    import app.widgets.chassis as mod

    class VectorPixmap(QSvgGenerator):
        def __init__(self, w, h):
            super().__init__()
            self.buf = QBuffer()
            self.buf.open(QIODevice.OpenModeFlag.WriteOnly)
            self.setOutputDevice(self.buf)
            self.setSize(QSize(int(w), int(h)))
            self.setViewBox(QRect(0, 0, int(w), int(h)))
            self.bg = None
            self.w, self.h = int(w), int(h)

        def setDevicePixelRatio(self, _r):
            pass

        def fill(self, c):
            self.bg = QColor(c).name()

    real = mod.QPixmap
    mod.QPixmap = VectorPixmap
    try:
        pm = ch._render_ground()
    finally:
        mod.QPixmap = real
    body = svg.clean(bytes(pm.buf.data()).decode("utf-8"))
    under = (f'<rect id="{svg.ident("Ground fill")}" x="0" y="0" width="{pm.w}" height="{pm.h}" '
             f'fill="{pm.bg}"/>\n') if pm.bg else ""
    return under + body


def _chassis_layers(svg: _Svg, ch, root, parts: set[str]) -> list[str]:
    from PyQt6.QtCore import QPoint
    from app.widgets.chassis import Chassis
    pos = ch.mapTo(root, QPoint(0, 0)) if ch is not root else QPoint(0, 0)
    size = (root.width(), root.height())
    ch._t0 = time.monotonic()      # the lightfall at rest, the same every export
    shift = f"translate({pos.x()},{pos.y()})" if (pos.x() or pos.y()) else None

    def painted(fn):
        def go(p):
            p.translate(pos.x(), pos.y())
            fn(p)
        return svg.fragment(size, go)
    out = []
    if "frame" in parts or ("face" in parts and ch.plate_tint()):
        label = "Ground" if "frame" in parts else "Plate tint (this face's own)"
        out.append(svg.group(label, _vector_ground(svg, ch), shift))
    if parts == {"face"} and svg.reference:
        out.append(svg.group("Reference: the frame (a picture; edit it in overhead-master.svg)",
                             f'<image x="0" y="0" width="{root.width()}" height="{root.height()}" '
                             f'xlink:href="{svg.reference}"/>\n'))
    if "frame" in parts and ch.dark:
        out.append(svg.group("Lightfall", painted(ch._paint_lightfall)))
    if type(ch).paint_plate_content is not Chassis.paint_plate_content:
        if "face" in parts:
            out.append(svg.group("Plate content", painted(ch.paint_plate_content)))
        return out
    if "frame" in parts:
        out.append(svg.group("Header", painted(ch.paint_header)))
    if "face" in parts:
        out.append(svg.group("Stage", painted(lambda p: ch.paint_stage(p, ch.stage_rect()))))
    if "frame" in parts:
        out.append(svg.group("Footer", painted(lambda p: ch.paint_footer(p, ch.footer_rect()))))
    return out


def _walk(svg: _Svg, w, root, parts: str, under_face: bool = False) -> str:
    """`parts`: "all" (every widget), "frame" (only the chassis frame),
    "face" (the chassis's own content and every widget on it)."""
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QRegion
    from PyQt6.QtWidgets import QWidget
    from app.widgets.chassis import Chassis
    if w is not root and (not w.isVisibleTo(root) or _clipped_out(w, root)):
        return ""
    cls = type(w).__name__
    pos = w.mapTo(root, QPoint(0, 0)) if w is not root else QPoint(0, 0)
    if "WebEngine" in cls:
        if parts == "frame":
            return ""
        box = (f'<rect x="{pos.x()}" y="{pos.y()}" width="{w.width()}" height="{w.height()}" '
               f'fill="none" stroke="#6A6462" stroke-width="2" stroke-dasharray="10 8"/>\n')
        return svg.group("Web view (the CAD model): not exported", box)
    size = (root.width(), root.height())
    is_ch = isinstance(w, Chassis)
    items: list[str] = []
    own = ""
    if is_ch:
        items += _chassis_layers(svg, w, root, {"frame", "face"} if parts == "all" else {parts})
    elif parts == "all" or (parts == "face" and under_face):
        own = svg.fragment(size, lambda p: w.render(p, pos, QRegion(),
                                                    QWidget.RenderFlag.DrawWindowBackground))
        items.append(own)
    for c in w.children():
        if not isinstance(c, QWidget) or c.isWindow():
            continue
        if is_ch and _is_frame(c):
            # The dwell rail sits in the footer: the frame's, not the face's.
            if parts in ("all", "frame"):
                items.append(_walk(svg, c, root, "all"))
            continue
        if is_ch and parts == "frame":
            continue
        items.append(_walk(svg, c, root, parts, under_face or is_ch))
    items = [i for i in items if i.strip()]
    if not items:
        return ""
    if len(items) == 1 and not is_ch and not own:
        return items[0]                 # a container that paints nothing: no extra level
    return svg.group(_label(w), "".join(items))


def _clipped_out(w, root) -> bool:
    """Wholly outside a parent's rect (scrolled out of view). Qt's own
    `visibleRegion()` is empty for everything in an offscreen window."""
    from PyQt6.QtCore import QPoint, QRect
    r = QRect(w.mapTo(root, QPoint(0, 0)), w.size())
    parent = w.parentWidget()
    while parent is not None and parent is not root:
        r = r.intersected(QRect(parent.mapTo(root, QPoint(0, 0)), parent.size()))
        if r.isEmpty():
            return True
        parent = parent.parentWidget()
    return r.intersected(root.rect()).isEmpty()


def _is_frame(widget) -> bool:
    """A chassis child that belongs to the frame (the footer's dwell rail)."""
    return type(widget).__name__ == "SmoothRail"


def _chassis_of(win):
    from app.widgets.chassis import Chassis
    shown = [c for c in win.findChildren(Chassis) if c.isVisibleTo(win)]
    return max(shown, key=lambda c: c.width() * c.height()) if shown else None


def _frame_png(win, path: Path) -> None:
    """The frame a face sits in, as a picture: the reference under a face
    file's content (the frame's vectors live once, in overhead-master). A
    face that tints its plate carries the tint itself, so then only the
    header, footer and rail are in the picture."""
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QImage, QPainter
    from app.widgets.chassis import Chassis
    img = QImage(OVERHEAD_W, OVERHEAD_H, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    ch = _chassis_of(win)
    if ch is not None:
        pos = ch.mapTo(win, QPoint(0, 0))
        ch._t0 = time.monotonic()
        p.save()
        p.translate(pos.x(), pos.y())
        if not ch.plate_tint():
            p.drawPixmap(0, 0, ch._render_ground())
            if ch.dark:
                ch._paint_lightfall(p)
        if type(ch).paint_plate_content is Chassis.paint_plate_content:
            ch.paint_header(p)
            ch.paint_footer(p, ch.footer_rect())
        p.restore()
        for c in ch.children():
            if _is_frame(c) and c.isVisibleTo(win):
                c.render(p, c.mapTo(win, QPoint(0, 0)))
    p.end()
    img.save(str(path))


def _elsewhere(t: Target, win) -> bool:
    """In a Slide's file, is this screen the half another file owns? The
    description file owns the Description screen; a Detail file its Detail."""
    if t.surface != "overhead" or not t.what.startswith("slide:"):
        return False
    entry = win._slides.current_slide
    is_description = entry is not None and not entry.face
    return is_description != (t.what == "slide:description")


def _parts(t: Target) -> str:
    if t.surface != "overhead":
        return "all"
    return "frame" if t.what == "master" else "face"


def _write_svg(obj, t: Target, path: Path) -> None:
    from xml.sax.saxutils import escape
    svg = _Svg()
    w, h = t.size
    parts = _parts(t)
    body = ""
    if isinstance(obj, _Pair):
        for side, win, x in (("A", obj.a, 0), ("B", obj.b, OVERHEAD_W + GAP)):
            svg.reference = ""
            if _elsewhere(t, win):
                # This half of a Slide is another file's: the Description's
                # layout lives once (slide-description), each Detail's once.
                ref = path.parent / "refs" / f"{path.stem}.screen-{side.lower()}.png"
                ref.parent.mkdir(exist_ok=True)
                win.grab().save(str(ref))
                other = "overhead-slide-description.svg" if t.what != "slide:description" \
                    else "its Detail's file"
                body += svg.group(f"Screen {side}", svg.group(
                    f"Reference: this half is edited in {other} (a picture)",
                    f'<image x="0" y="0" width="{OVERHEAD_W}" height="{OVERHEAD_H}" '
                    f'xlink:href="refs/{ref.name}"/>\n'),
                    f"translate({x},0)" if x else None, layer=True)
                continue
            # Judges' artwork isn't on the chassis: no shared frame, so all of it.
            here = parts if _chassis_of(win) is not None else "all"
            if here == "face":
                ref = path.parent / "refs" / f"{path.stem}.frame-{side.lower()}.png"
                ref.parent.mkdir(exist_ok=True)
                _frame_png(win, ref)
                svg.reference = f"refs/{ref.name}"
            body += svg.group(f"Screen {side}", _walk(svg, win, win, here),
                              f"translate({x},0)" if x else None, layer=True)
    else:
        inner = _walk(svg, obj, obj, "all")
        if not inner.lstrip().startswith("<g "):
            inner = svg.group(_label(obj), inner)
        body = inner.replace("<g ", '<g i:layer="yes" ', 1)
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
           f'<svg width="{w}px" height="{h}px" viewBox="0 0 {w} {h}" '
           f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'xmlns:i="http://ns.adobe.com/AdobeIllustrator/10.0/" '
           f'version="1.2" baseProfile="tiny">\n'
           f'<title>{escape("Pit Display " + t.spec())}</title>\n'
           f'<desc>design.py target={t.spec()} theme={t.theme} mode={t.mode} size={w}x{h}</desc>\n'
           f'{body}</svg>\n')
    path.write_text(doc, encoding="utf-8")


def _blank(img) -> bool:
    w, h = img.width(), img.height()
    seen = {img.pixel(int(x * w / 24), int(y * h / 14)) for x in range(24) for y in range(14)}
    return len(seen) < 3


def render_main(argv: list[str]) -> int:
    """`_render <spec> --theme --mode --size --png P --svg P`, or `_render --all`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--mode", default="standard")
    ap.add_argument("--size")
    ap.add_argument("--png")
    ap.add_argument("--svg")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    o = ap.parse_args(argv)
    _boot()
    if o.list:
        print("\n".join(all_targets(list(_slide_stops()))))
        sys.stdout.flush()
        os._exit(0)
    if o.all:
        bad = 0
        for spec in all_targets(list(_slide_stops())):
            t = parse_target(spec)
            try:
                w = _build(t)
                img = w.grab().toImage()
                ok = not _blank(img)
                _write_svg(w, t, SCRATCH / "check.svg")    # the layered writer, every surface
                ET.parse(SCRATCH / "check.svg")
                w.close()
                w.deleteLater()
                _settle(0.05)
            except BaseException as e:  # noqa: BLE001  (a SystemExit from a bad name too)
                ok, img = False, None
                print(f"  ✗ {spec}  — {type(e).__name__}: {e}", flush=True)
                bad += 1
                continue
            print(f"  {'✓' if ok else '✗'} {spec}" + ("" if ok else "  — painted blank"), flush=True)
            bad += not ok
        print(f"\n{'All screens draw.' if not bad else f'{bad} screens failed.'}")
        os._exit(1 if bad else 0)
    t = parse_target(o.spec, o.theme, o.mode, o.size)
    w = _build(t)
    if o.png:
        w.grab().save(o.png)
    if o.svg:
        _write_svg(w, t, Path(o.svg))
    sys.stdout.flush()
    os._exit(0)   # skip Qt's teardown: services' threads die with the process


def render(t: Target, png: Path | None = None, svg: Path | None = None) -> None:
    """Paint one target in a fresh process (a clean boot each time, and code just saved)."""
    args = [sys.executable, str(Path(__file__).resolve()), "_render", t.spec(),
            "--theme", t.theme, "--mode", t.mode]
    if t.fixed:
        args += ["--size", f"{t.size[0]}x{t.size[1]}"]
    if png:
        args += ["--png", str(png)]
    if svg:
        args += ["--svg", str(svg)]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    made = [p for p in (png, svg) if p]
    if r.returncode != 0 or not all(p.exists() for p in made):
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-15:])
        raise RuntimeError(f"couldn't draw {t.spec()}:\n{tail}")


# ── Reading an SVG (ours or Illustrator's) ───────────────────────────────────

_NAMED = {"black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "none": None,
          "transparent": None}


def color(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if v.lower() in _NAMED:
        return _NAMED[v.lower()]
    if v.startswith("url("):
        return None
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1)).upper()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", v)
    if m:
        return "#" + m.group(1).upper()
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)%?\s*,\s*([\d.]+)%?\s*,\s*([\d.]+)%?.*\)", v)
    if m:
        pct = "%" in v
        return "#" + "".join(f"{round(float(c) * (2.55 if pct else 1)):02X}" for c in m.groups())
    return None


def _num(v: str | None, default: float = 0.0) -> float:
    if v is None:
        return default
    m = re.match(r"\s*(-?[\d.]+(?:e-?\d+)?)\s*(px|pt)?", v)
    if not m:
        return default
    n = float(m.group(1))
    return n * 4 / 3 if m.group(2) == "pt" else n


def _matrix(transform: str | None):
    a, b, c, d, e, f = 1, 0, 0, 1, 0, 0
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", transform or ""):
        n = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix" and len(n) == 6:
            m = n
        elif name == "translate":
            m = [1, 0, 0, 1, n[0], n[1] if len(n) > 1 else 0]
        elif name == "scale":
            m = [n[0], 0, 0, n[1] if len(n) > 1 else n[0], 0, 0]
        elif name == "rotate":
            r = math.radians(n[0])
            m = [math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0]
        else:
            continue
        a, b, c, d, e, f = (a * m[0] + c * m[1], b * m[0] + d * m[1],
                            a * m[2] + c * m[3], b * m[2] + d * m[3],
                            a * m[4] + c * m[5] + e, b * m[4] + d * m[5] + f)
    return a, b, c, d, e, f


def _mul(p, q):
    a, b, c, d, e, f = p
    return (a * q[0] + c * q[1], b * q[0] + d * q[1], a * q[2] + c * q[3],
            b * q[2] + d * q[3], a * q[4] + c * q[5] + e, b * q[4] + d * q[5] + f)


_WEIGHTS = {"thin": 100, "extralight": 200, "light": 300, "regular": 400, "book": 400,
            "medium": 500, "semibold": 600, "demibold": 600, "bold": 700, "extrabold": 800,
            "black": 900}


def font(family: str, weight: str = "") -> tuple[str, str]:
    """(family key, weight) the same for Qt's "Chakra Petch" 700 and
    Illustrator's PostScript "ChakraPetch-Bold"."""
    fam = family.split(",")[0].strip().strip("'\"")
    base, _, style = fam.partition("-")
    key = re.sub(r"[^a-z0-9]", "", base.casefold())
    w = str(weight or "").strip().casefold()
    w = {"normal": "400", "bold": "700"}.get(w, w)
    style_key = re.sub(r"[^a-z]", "", style.casefold().replace("italic", ""))
    if style_key in _WEIGHTS:
        w = str(_WEIGHTS[style_key])
    return key, w or "400"


@dataclass
class Run:
    text: str
    x: float
    y: float
    size: float
    family: str
    weight: str
    fill: str | None

    @property
    def key(self) -> str:
        return " ".join(self.text.split()).casefold()


@dataclass
class Drawing:
    width: float = 0
    height: float = 0
    runs: list[Run] = field(default_factory=list)
    fills: Counter = field(default_factory=Counter)      # colour → shapes painted with it
    reds: int = 0                                         # shapes and text in the red family
    fonts: set[str] = field(default_factory=set)
    desc: str = ""


_STYLE_KEYS = ("fill", "stroke", "font-family", "font-size", "font-weight", "opacity",
               "fill-opacity", "display", "visibility")
_SHAPES = {"rect", "path", "circle", "ellipse", "polygon", "polyline", "line"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_svg(path: Path) -> Drawing:
    root = ET.parse(path).getroot()
    out = Drawing()
    vb = [float(v) for v in re.split(r"[\s,]+", root.get("viewBox", "").strip()) if v]
    out.width, out.height = (vb[2], vb[3]) if len(vb) == 4 else (_num(root.get("width")),
                                                                  _num(root.get("height")))
    classes: dict[str, dict[str, str]] = {}
    for el in root.iter():
        if _local(el.tag) == "style" and el.text:
            for sels, body in re.findall(r"([^{}]+)\{([^}]*)\}", el.text):
                decl = dict((k.strip(), v.strip()) for k, _, v in
                            (d.partition(":") for d in body.split(";") if ":" in d))
                for sel in sels.split(","):
                    sel = sel.strip()
                    if sel.startswith("."):
                        classes.setdefault(sel[1:], {}).update(decl)
        if _local(el.tag) == "desc" and el.text:
            out.desc = el.text.strip()

    def style_of(el, inherited: dict) -> dict:
        s = dict(inherited)
        for cls in (el.get("class") or "").split():
            s.update(classes.get(cls, {}))
        for k in _STYLE_KEYS:
            if el.get(k) is not None:
                s[k] = el.get(k)
        for d in (el.get("style") or "").split(";"):
            k, _, v = d.partition(":")
            if k.strip():
                s[k.strip()] = v.strip()
        return s

    def walk(el, m, inherited):
        tag = _local(el.tag)
        if tag in ("defs", "style", "title", "desc", "clipPath", "mask", "linearGradient",
                   "radialGradient", "symbol", "metadata"):
            return
        s = style_of(el, inherited)
        if s.get("display") == "none" or s.get("visibility") == "hidden":
            return
        m = _mul(m, _matrix(el.get("transform")))
        if tag == "text":
            _text(el, m, s, out, style_of)
            return
        if tag in _SHAPES:
            fill = color(s.get("fill", "#000000" if tag != "line" and tag != "polyline" else None))
            stroke = color(s.get("stroke"))
            if _num(s.get("opacity"), 1) * _num(s.get("fill-opacity"), 1) > 0.05 and fill:
                out.fills[fill] += 1
                out.reds += is_red(fill)
            if stroke:
                out.fills[stroke] += 1
        for child in el:
            walk(child, m, s)

    walk(root, (1, 0, 0, 1, 0, 0), {})
    return out


def _text(el, m, s, out: Drawing, style_of) -> None:
    def add(text, node_style, x, y):
        text = " ".join(text.split())
        if not text:
            return
        px = m[0] * x + m[2] * y + m[4]
        py = m[1] * x + m[3] * y + m[5]
        scale = math.hypot(m[0], m[1]) or 1
        family = node_style.get("font-family", "").split(",")[0].strip().strip("'\"")
        _, weight = font(family, node_style.get("font-weight", ""))
        fill = color(node_style.get("fill", "#000000"))
        out.runs.append(Run(text, px, py, _num(node_style.get("font-size"), 16) * scale,
                            family, weight, fill))
        if family:
            out.fonts.add(family)
        out.reds += is_red(fill)

    x, y = _num(el.get("x")), _num(el.get("y"))
    head = el.text or ""
    tspans = [c for c in el if _local(c.tag) == "tspan"]
    if head.strip():
        add(head, s, x, y)
    for t in tspans:
        ts = style_of(t, s)
        tx = _num(t.get("x"), x) if t.get("x") else x
        ty = _num(t.get("y"), y) if t.get("y") else y
        if t.get("x") is None and t.get("y") is None and not head.strip() and len(tspans) > 1:
            pass
        add("".join(t.itertext()), ts, tx, ty)
        if t.tail and t.tail.strip():
            add(t.tail, s, tx, ty)


# ── The brand ────────────────────────────────────────────────────────────────

def palette() -> dict[str, str]:
    """Every colour the brand allows: `app/brand.py`'s tokens and the playbook's span."""
    tokens: dict[str, str] = {}
    for name, hexv in re.findall(r'^(\w+)\s*=\s*"(#[0-9A-Fa-f]{6})"',
                                 (ROOT / "app" / "brand.py").read_text(), re.M):
        tokens.setdefault(hexv.upper(), name)
    for hexv in re.findall(r'"(#[0-9A-Fa-f]{6})"', (ROOT / "app" / "brand.py").read_text()):
        tokens.setdefault(hexv.upper(), "brand.py")
    md = (ROOT / "breakaway_branding.md").read_text()
    for name, hexv in re.findall(r"\|\s*\**([A-Za-z][\w ]*?)\**\s*\|\s*\**`(#[0-9A-Fa-f]{6})`", md):
        tokens.setdefault(hexv.upper(), name.strip())
    for hexv in re.findall(r"`(#[0-9A-Fa-f]{6})`", md):
        tokens.setdefault(hexv.upper(), "playbook")
    tokens.setdefault("#000000", "black (shadows only)")
    return tokens


def _dist(a: str, b: str) -> float:
    ra, ga, ba = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    rb, gb, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return math.sqrt(2 * (ra - rb) ** 2 + 4 * (ga - gb) ** 2 + 3 * (ba - bb) ** 2) / 3


def nearest(hexv: str, pal: dict[str, str]) -> tuple[str, float]:
    best = min(pal, key=lambda p: _dist(hexv, p))
    return best, _dist(hexv, best)


def lint_colors(colors, pal) -> list[str]:
    notes = []
    for c in sorted(set(colors)):
        if c in pal:
            continue
        best, d = nearest(c, pal)
        if d <= NEAR_MISS:
            notes.append(f"{c} is {pal[best]} {best}, drifted; use {best}")
        else:
            notes.append(f"{c} is off the palette (nearest {pal[best]} {best})")
    return notes


def lint_drawing(d: Drawing, pal, baseline: Drawing | None = None) -> list[str]:
    notes = lint_colors(list(d.fills) + [r.fill for r in d.runs if r.fill], pal)
    # Grey ramps from Qt's anti-aliased ground gradients aren't choices; ours
    # are judged only where the edit brought them in.
    if baseline is not None:
        theirs = set(baseline.fills) | {r.fill for r in baseline.runs}
        notes = [n for n in notes if n.split()[0] not in theirs]
    known = {re.sub(r"[^a-z0-9]", "", f) for f in FONTS}
    for f in sorted(d.fonts):
        low = f.casefold()
        if font(f)[0] in known:
            continue
        if "ardestine" in low:
            notes.append(f"font {f}: logo artwork only, never live text")
        else:
            notes.append(f"font {f}: the brand's faces are Chakra Petch (headlines, numbers), "
                         "Roboto (sentences), JetBrains Mono (data)")
    for r in d.runs:
        if is_red(r.fill) and len(r.text) > 2:
            notes.append(f"red text \"{r.text[:40]}\": on a dark ground red is a filled shape, "
                         "never a letterform (2.8:1)")
    if baseline is not None and d.reds > baseline.reds:
        notes.append(f"more red: {d.reds} red things, the code's screen has {baseline.reds} "
                     "(one red thing per surface)")
    return notes


# ── Diffing two drawings ─────────────────────────────────────────────────────

def _where(x: float, y: float, w: float, h: float) -> str:
    if w == SIZES["overhead"][0]:            # the matched set: say which screen
        if x >= OVERHEAD_W + GAP:
            return "Screen B, " + _where(x - OVERHEAD_W - GAP, y, OVERHEAD_W, h)
        if x <= OVERHEAD_W:
            return "Screen A, " + _where(x, y, OVERHEAD_W, h)
        return "between the screens"
    col = "left" if x < w / 3 else "right" if x > 2 * w / 3 else "centre"
    row = "top" if y < h / 3 else "bottom" if y > 2 * h / 3 else "middle"
    return f"{row} {col}" if (row, col) != ("middle", "centre") else "centre"


def diff_drawings(code: Drawing, edit: Drawing) -> list[str]:
    sx = code.width / edit.width if edit.width else 1
    sy = code.height / edit.height if edit.height else 1
    for r in edit.runs:      # an artboard resized in Illustrator still lines up
        r.x, r.y, r.size = r.x * sx, r.y * sy, r.size * sx
    changes: list[str] = []
    left = list(code.runs)
    added: list[Run] = []
    for r in edit.runs:
        same = [c for c in left if c.key == r.key]
        if not same:
            added.append(r)
            continue
        c = min(same, key=lambda c: math.hypot(c.x - r.x, c.y - r.y))
        left.remove(c)
        what = []
        dx, dy = r.x - c.x, r.y - c.y
        if math.hypot(dx, dy) > MOVED_PX:
            what.append(f"moved {dx:+.0f}, {dy:+.0f} px (to {r.x:.0f}, {r.y:.0f})")
        if abs(r.size - c.size) > SIZE_PX:
            what.append(f"size {c.size:.0f} → {r.size:.0f} px")
        if r.fill != c.fill:
            what.append(f"colour {c.fill} → {r.fill}")
        if r.family and c.family and font(r.family)[0] != font(c.family)[0]:
            what.append(f"font {c.family} → {r.family}")
        if r.weight != c.weight and r.weight and c.weight:
            what.append(f"weight {c.weight} → {r.weight}")
        if what:
            changes.append(f"\"{c.text[:50]}\": " + "; ".join(what))
    # Reworded: a removed and an added text at about the same place.
    for r in list(added):
        near = [c for c in left if math.hypot(c.x - r.x, c.y - r.y) < 40]
        if near:
            c = min(near, key=lambda c: math.hypot(c.x - r.x, c.y - r.y))
            left.remove(c)
            added.remove(r)
            bits = [f"size {c.size:.0f} → {r.size:.0f} px"] if abs(r.size - c.size) > SIZE_PX else []
            if r.fill != c.fill:
                bits.append(f"colour {c.fill} → {r.fill}")
            if r.family and c.family and font(r.family)[0] != font(c.family)[0]:
                bits.append(f"font {c.family} → {r.family}")
            extra = f" ({'; '.join(bits)})" if bits else ""
            changes.append(f"reworded at {r.x:.0f}, {r.y:.0f}: \"{c.text[:50]}\" → \"{r.text[:60]}\"{extra}")
    for r in added:
        changes.append(f"new text at {r.x:.0f}, {r.y:.0f} ({_where(r.x, r.y, code.width, code.height)}): "
                       f"\"{r.text[:60]}\", {r.family or '?'} {r.size:.0f} px {r.weight}, {r.fill}")
    for c in left:
        changes.append(f"removed text at {c.x:.0f}, {c.y:.0f}: \"{c.text[:60]}\"")
    for col in sorted(set(edit.fills) - set(code.fills)):
        changes.append(f"new colour {col} on {edit.fills[col]} shape(s)")
    for col in sorted(set(code.fills) - set(edit.fills)):
        if code.fills[col] >= 1 and col not in {r.fill for r in edit.runs}:
            changes.append(f"colour {col} no longer used ({code.fills[col]} shape(s) in the code's screen)")
    return changes


def _qt():
    """A QApplication for painting SVGs, kept alive (an unreferenced one is collected)."""
    global _APP
    from PyQt6.QtWidgets import QApplication
    if QApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _APP = QApplication(["design"])
    return QApplication.instance()


def picture_diff(code_svg: Path, edit_svg: Path, out_png: Path) -> list[str]:
    """Both painted at the code's size; changed regions boxed on your edit. → region list."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPen
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtWidgets import QApplication
    _qt()

    def own(path: Path) -> bytes:
        # A linked picture (the frame reference) is context, not the edit;
        # it also resolves differently beside each file. Compare without it.
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r'<image\b(?![^>]*href="data:)[^>]*/>', "", text)
        return text.encode("utf-8")
    from PyQt6.QtCore import QByteArray
    a, b = QSvgRenderer(QByteArray(own(code_svg))), QSvgRenderer(QByteArray(own(edit_svg)))
    vb = a.viewBoxF()
    w, h = int(vb.width()) or 1920, int(vb.height()) or 1080

    def paint(r):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor("#000000"))
        p = QPainter(img)
        r.render(p, QRectF(0, 0, w, h))
        p.end()
        return img
    ia, ib = paint(a), paint(b)
    cells = []
    step = 4
    for cy in range(0, h, CELL):
        for cx in range(0, w, CELL):
            n = changed = 0
            for y in range(cy, min(cy + CELL, h), step):
                for x in range(cx, min(cx + CELL, w), step):
                    pa, pb = ia.pixel(x, y), ib.pixel(x, y)
                    n += 1
                    if any(abs(((pa >> s) & 255) - ((pb >> s) & 255)) > PIXEL_DIFF for s in (0, 8, 16)):
                        changed += 1
            if n and changed / n > CELL_CHANGED:
                cells.append((cx, cy))
    regions = _merge_cells(cells)
    # code | your edit, regions boxed on both
    sheet = QImage(w * 2 + 40, h, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#808080"))
    p = QPainter(sheet)
    p.drawImage(0, 0, ia)
    p.drawImage(w + 40, 0, ib)
    pen = QPen(QColor("#E08A1E"))
    pen.setWidth(4)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for x0, y0, x1, y1 in regions:
        p.drawRect(x0, y0, x1 - x0, y1 - y0)
        p.drawRect(w + 40 + x0, y0, x1 - x0, y1 - y0)
    p.end()
    sheet.save(str(out_png))
    return [f"{x0}-{x1} × {y0}-{y1} px ({_where((x0 + x1) / 2, (y0 + y1) / 2, w, h)})"
            for x0, y0, x1, y1 in regions]


def _merge_cells(cells: list[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """Touching changed cells → one box each."""
    todo, boxes = set(cells), []
    while todo:
        stack = [todo.pop()]
        group = []
        while stack:
            c = stack.pop()
            group.append(c)
            for dx in (-CELL, 0, CELL):
                for dy in (-CELL, 0, CELL):
                    n = (c[0] + dx, c[1] + dy)
                    if n in todo:
                        todo.remove(n)
                        stack.append(n)
        xs, ys = [c[0] for c in group], [c[1] for c in group]
        boxes.append((min(xs), min(ys), max(xs) + CELL, max(ys) + CELL))
    return sorted(boxes, key=lambda b: (b[1], b[0]))


# ── Commands ─────────────────────────────────────────────────────────────────

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exports() -> dict:
    try:
        return json.loads(EXPORTS.read_text())
    except (OSError, ValueError):
        return {}


def cmd_export(o) -> int:
    if o.target == "all":
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_render", "--list"],
                           cwd=ROOT, capture_output=True, text=True)
        specs = [ln for ln in r.stdout.splitlines() if ":" in ln]
        failed = 0
        for spec in specs:
            o.target = spec
            try:
                failed += cmd_export(o) != 0
            except RuntimeError as e:
                print(e)
                failed += 1
        print(f"\n{len(specs) - failed} of {len(specs)} exported into design/screens/")
        return 1 if failed else 0
    t = parse_target(o.target, o.theme, o.mode, o.size)
    SCREENS.mkdir(parents=True, exist_ok=True)
    (SCREENS / "refs").mkdir(exist_ok=True)
    svg, png = SCREENS / f"{t.name}.svg", SCREENS / "refs" / f"{t.name}.png"
    known = _exports()
    if svg.exists() and not o.force and known.get(svg.name) != _sha(svg):
        print(f"{svg.relative_to(ROOT)} has been edited since it was exported.\n"
              f"  See what changed:       uv run tools/design.py diff {t.name}\n"
              f"  Replace it with code's: uv run tools/design.py export {o.target} --force")
        return 1
    render(t, png=png, svg=svg)
    known[svg.name] = _sha(svg)
    EXPORTS.write_text(json.dumps(known, indent=2, sort_keys=True) + "\n")
    d = read_svg(svg)
    print(f"{svg.relative_to(ROOT)}  ({d.width:.0f}×{d.height:.0f}, {t.theme}, {t.mode})")
    print(f"{png.relative_to(ROOT)}  (how the app draws it, for reference)")
    return 0


def _find_svg(name: str) -> Path:
    p = Path(name)
    for cand in (p, ROOT / p, SCREENS / p.name, SCREENS / f"{p.name}.svg", DESIGN / "inbox" / p.name):
        if cand.is_file():
            return cand
    raise SystemExit(f"no SVG {name!r} (looked in design/screens and design/inbox)")


def cmd_diff(o) -> int:
    edit_path = _find_svg(o.file)
    edit = read_svg(edit_path)
    m = re.search(r"target=(\S+) theme=(\S+) mode=(\S+) size=(\S+)", edit.desc)
    if o.target:
        t = parse_target(o.target, o.theme, o.mode, o.size)
    elif m:   # Qt's <desc> survives an Illustrator save only sometimes; the name always
        t = parse_target(m.group(1), m.group(2), m.group(3))
    else:
        t = parse_target(edit_path.name)
    OUT.mkdir(parents=True, exist_ok=True)
    code_svg = OUT / f"{t.name}.code.svg"
    print(f"Drawing {t.spec()} ({t.theme}, {t.mode}) from the code…", flush=True)
    render(t, svg=code_svg)
    code = read_svg(code_svg)
    changes = diff_drawings(code, read_svg(edit_path))
    regions = picture_diff(code_svg, edit_path, OUT / f"{t.name}.diff.png")
    notes = lint_drawing(read_svg(edit_path), palette(), baseline=code)

    lines = [f"# {edit_path.relative_to(ROOT) if edit_path.is_relative_to(ROOT) else edit_path}"
             f" against the code ({t.spec()}, {t.theme}, {t.mode})", ""]
    if not changes and not regions:
        lines.append("The code matches this file.")
    if changes:
        lines += ["## What changed", ""] + [f"- {c}" for c in changes] + [""]
    if regions:
        lines += ["## Where pixels changed (boxed in the picture)", ""] + [f"- {r}" for r in regions] + [""]
        lines.append(f"Picture: `{(OUT / f'{t.name}.diff.png').relative_to(ROOT)}` (code | your edit)")
        lines.append("")
    if notes:
        lines += ["## Brand check", ""] + [f"- {n}" for n in notes] + [""]
    report = "\n".join(lines)
    (OUT / f"{t.name}.changes.md").write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"(saved as design/out/{t.name}.changes.md)")
    return 0


def cmd_lint(o) -> int:
    pal = palette()
    bad = 0
    for name in o.files:
        p = Path(name)
        if p.is_dir():
            files = [f for f in p.rglob("*") if f.suffix.lower() in (".svg", ".idml", ".css", ".py", ".qss")]
        else:
            files = [p]
        for f in files:
            notes = lint_file(f, pal)
            if notes:
                bad += 1
                print(f"{f}:")
                for n in notes:
                    print(f"  - {n}")
    if not bad:
        print("On brand.")
    return 1 if bad else 0


def lint_file(f: Path, pal) -> list[str]:
    suf = f.suffix.lower()
    if suf == ".svg":
        return lint_drawing(read_svg(f), pal)
    if suf == ".idml":
        return _lint_idml(f, pal)
    if suf in (".ai", ".pdf", ".indd", ".psd"):
        return [f"can't read {suf}: export it as SVG (Illustrator) or IDML (InDesign); see design/README.md"]
    text = f.read_text(encoding="utf-8", errors="replace")
    found = [color(h) for h in re.findall(r"#[0-9A-Fa-f]{6}\b", text)]
    return lint_colors([c for c in found if c], pal)


def _lint_idml(f: Path, pal) -> list[str]:
    import zipfile
    notes, cols = [], []
    with zipfile.ZipFile(f) as z:
        for name in z.namelist():
            if not name.endswith(".xml"):
                continue
            x = z.read(name).decode("utf-8", "replace")
            for space, val in re.findall(r'<Color [^>]*Space="(RGB|CMYK)"[^>]*ColorValue="([^"]+)"', x):
                n = [float(v) for v in val.split()]
                if space == "RGB":
                    rgb = n[:3]
                else:   # a print conversion: near enough to spot drift, not to proof
                    c, m, y, k = (v / 100 for v in n[:4])
                    rgb = [255 * (1 - c) * (1 - k), 255 * (1 - m) * (1 - k), 255 * (1 - y) * (1 - k)]
                cols.append("#" + "".join(f"{round(v):02X}" for v in rgb))
            for fam in re.findall(r'(?:AppliedFont|FontFamily)[^>]*?>(?:<[^>]*>)?([^<]+)<', x) + \
                    re.findall(r'FontFamily="([^"]+)"', x):
                low = fam.casefold()
                if fam.strip() and not any(ok in low for ok in FONTS) and "ardestine" not in low:
                    notes.append(f"font {fam.strip()}: not one of the brand's faces")
    return sorted(set(notes)) + lint_colors(cols, pal)


def cmd_shot(o) -> int:
    t = parse_target(o.target, o.theme, o.mode, o.size)
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / f"{t.name}.png"
    render(t, png=png)
    print(png.relative_to(ROOT))
    if o.compare:
        mock = Path(o.compare)
        sheet = OUT / f"{t.name}.compare.png"
        _compare(mock, png, sheet)
        print(sheet.relative_to(ROOT), "(your mock-up | the app | the two overlaid)")
    return 0


def _compare(mock: Path, png: Path, out: Path) -> None:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer
    from PyQt6.QtWidgets import QApplication
    _qt()
    app_img = QImage(str(png))
    w, h = app_img.width(), app_img.height()
    if mock.suffix.lower() == ".svg":
        m = QImage(w, h, QImage.Format.Format_ARGB32)
        m.fill(QColor("#000000"))
        p = QPainter(m)
        QSvgRenderer(str(mock)).render(p)
        p.end()
    else:
        m = QImage(str(mock)).scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
    sheet = QImage(w * 3 + 80, h, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#808080"))
    p = QPainter(sheet)
    p.drawImage(0, 0, m)
    p.drawImage(w + 40, 0, app_img)
    p.drawImage(2 * w + 80, 0, app_img)
    p.setOpacity(0.5)
    p.drawImage(2 * w + 80, 0, m)
    p.end()
    sheet.save(str(out))


def cmd_watch(o) -> int:
    """A window showing the screen, redrawn whenever a file under app/ or assets/ is saved."""
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import QApplication, QLabel
    t = parse_target(o.target, o.theme, o.mode, o.size)
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / f"{t.name}.watch.png"
    app = QApplication(["design watch"])
    label = QLabel("Drawing…")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet("background:#121013; color:#F3F1F0; font: 14px monospace; padding: 12px;")
    label.setWordWrap(True)
    label.resize(min(t.size[0], 1280), int(min(t.size[0], 1280) * t.size[1] / t.size[0]))
    label.setWindowTitle(f"{t.spec()} · watching app/ and assets/")
    label.show()
    state = {"stamp": None, "proc": None, "pending": False}

    def stamp():
        newest = 0.0
        for base in (ROOT / "app", ROOT / "assets"):
            for p in base.rglob("*"):
                if p.suffix in (".py", ".css", ".js", ".qss", ".json", ".svg", ".png", ".ttf"):
                    try:
                        newest = max(newest, p.stat().st_mtime)
                    except OSError:
                        pass
        return newest

    def show():
        pix = QPixmap(str(png))
        if not pix.isNull():
            label.setPixmap(pix.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))

    def start():
        args = [sys.executable, str(Path(__file__).resolve()), "_render", t.spec(), "--theme",
                t.theme, "--mode", t.mode, "--png", str(png)]
        if t.fixed:
            args += ["--size", f"{t.size[0]}x{t.size[1]}"]
        state["proc"] = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True)
        label.setWindowTitle(f"{t.spec()} · drawing…")

    def tick():
        proc = state["proc"]
        if proc is not None and proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            state["proc"] = None
            if proc.returncode == 0:
                show()
                label.setWindowTitle(f"{t.spec()} · drawn {time.strftime('%H:%M:%S')}")
            else:
                label.setPixmap(QPixmap())
                label.setText("Couldn't draw it:\n\n" + "\n".join(out.strip().splitlines()[-25:]))
                label.setWindowTitle(f"{t.spec()} · error")
        s = stamp()
        if s != state["stamp"]:
            state["stamp"] = s
            state["pending"] = True
        if state["pending"] and state["proc"] is None:
            state["pending"] = False
            start()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(700)
    label.resizeEvent = lambda e: show()   # a throwaway window, not an app screen
    app.exec()
    if state["proc"] is not None:
        state["proc"].kill()
    return 0


def cmd_list(_o) -> int:
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_render", "--list"],
                       cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[-2000:])
    print("\nOptions: --theme light, --mode judges|lunch, --size WxH")
    return 0


def cmd_check(_o) -> int:
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_render", "--all"], cwd=ROOT)
    return r.returncode


def main() -> int:
    use_utf8()
    if len(sys.argv) > 1 and sys.argv[1] == "_render":
        return render_main(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def target_cmd(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("target")
        p.add_argument("--theme", default="dark", choices=["dark", "light"])
        p.add_argument("--mode", default="standard", choices=["standard", "judges", "lunch"])
        p.add_argument("--size", help="WxH, design pixels")
        p.set_defaults(fn=fn)
        return p
    target_cmd("export", cmd_export, "a screen as SVG into design/screens/").add_argument(
        "--force", action="store_true", help="write over an edited file")
    target_cmd("shot", cmd_shot, "a PNG into design/out/").add_argument(
        "--compare", help="a mock-up (PNG or SVG) to set beside it")
    target_cmd("watch", cmd_watch, "a live window, redrawn on save")
    p = sub.add_parser("diff", help="what an edited SVG changed against the code")
    p.add_argument("file")
    p.add_argument("--target", help="when the file's name doesn't say which screen")
    p.add_argument("--theme", default="dark")
    p.add_argument("--mode", default="standard")
    p.add_argument("--size")
    p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("lint", help="brand check: SVG, IDML, CSS, Python")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_lint)
    sub.add_parser("list", help="every screen it can draw").set_defaults(fn=cmd_list)
    sub.add_parser("check", help="every screen draws (exit 0/1)").set_defaults(fn=cmd_check)
    o = ap.parse_args()
    return o.fn(o)


if __name__ == "__main__":
    sys.exit(main())
