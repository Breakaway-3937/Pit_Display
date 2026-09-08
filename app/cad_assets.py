"""
CAD assets singleton — manages the local HTTP server, subsystems config,
and cross-component signals for the 3D CAD viewer feature.

The HTTP server is required so Three.js can fetch the GLTF model and
subsystems config from localhost (file:// origins block cross-origin fetches).

Usage anywhere:
    from app.cad_assets import cad_assets
    cad_assets.subsystem_focused.connect(my_slot)

Call init_cad_assets() once in main() after init_config().
"""

import functools
import http.server
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from app import paths
from app.lazy_proxy import LazyProxy

# The viewer page ships with the app and never changes at runtime; the model
# and its subsystem map are uploaded by the crew, so they live in the writable
# tree. `_LayeredHandler` below serves both as one directory.
_RES_ASSETS    = paths.resource("assets")
_DATA_ASSETS   = paths.data_dir("assets")
_CAD_DIR       = paths.data_dir("assets", "cad")
_SUBSYSTEMS    = _CAD_DIR / "subsystems.json"
_MODEL         = _CAD_DIR / "robot.glb"
# 8765 unless something says otherwise. `PIT_CAD_PORT` exists for exactly one
# caller: the updater runs the *staged* build's `--self-check` while this app is
# on screen, and a second server on 8765 would take the port out from under the
# CAD viewer a visitor is looking at. `0` means "any free port", which is what
# the verification run asks for.
_PORT          = int(os.environ.get("PIT_CAD_PORT") or 8765)

_DEFAULT_CONFIG: dict[str, Any] = {
    "_doc": (
        "Season CAD config — edit each year. "
        "node_name must match the Onshape sub-assembly name exactly (case-sensitive)."
    ),
    "season": "YYYY",
    "subsystems": [],
}


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """
    Serves the assets tree, **data first and resources second**.

    The viewer needs one URL space over two directories: `/cad_viewer/…` ships
    inside the bundle and `/cad/robot.glb` is whatever the crew last uploaded.
    Copying the shipped model into the data directory would work and would also
    duplicate a third of a gigabyte on every install, so the handler resolves
    each request against the writable tree and falls back to the read-only one.
    """

    def translate_path(self, path: str) -> str:
        # Resolve against the data tree using the base class' own sanitising —
        # it is what strips `..` and query strings, and reimplementing that is
        # how directory traversal bugs get written.
        self.directory = str(_DATA_ASSETS)
        resolved = super().translate_path(path)
        if Path(resolved).exists():
            return resolved
        self.directory = str(_RES_ASSETS)
        return super().translate_path(path)

    def log_message(self, *_):
        pass


class _HTTPServer(http.server.HTTPServer):
    allow_reuse_address = True


class _CADAssets(QObject):

    # subsystems.json was saved — CAD viewers and subsystem pickers should refresh
    config_changed = pyqtSignal()

    # robot.glb was replaced — CAD viewers should reload()
    model_changed = pyqtSignal()

    # A subsystem was focused from the control screen ("" = full view)
    subsystem_focused = pyqtSignal(str)

    # True = presentation screens should show the CAD page instead of slides
    cad_active_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self._server: http.server.HTTPServer | None = None
        self._port = _PORT
        self._cad_active = False
        self._focused_id = ""

    # ── HTTP server ───────────────────────────────────────────────────────

    def start_server(self) -> None:
        if self._server is not None:
            return
        handler = functools.partial(_SilentHandler, directory=str(_DATA_ASSETS))
        self._server = _HTTPServer(("127.0.0.1", _PORT), handler)
        # Port 0 means the OS chose one; read back what it actually bound so
        # `viewer_url` points somewhere real.
        self._port = self._server.server_address[1]
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()

    # ── URLs ──────────────────────────────────────────────────────────────

    @property
    def viewer_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/cad_viewer/index.html"

    @property
    def port(self) -> int:
        return self._port

    # ── Model file ────────────────────────────────────────────────────────

    @property
    def model_exists(self) -> bool:
        return self.model_path.exists()

    @property
    def model_path(self) -> Path:
        """The uploaded model if there is one, else the one that shipped."""
        return paths.find("assets", "cad", "robot.glb")

    def import_model(self, source: Path) -> None:
        """Copy a .glb file from source into assets/cad/robot.glb."""
        _CAD_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, _MODEL)
        self.model_changed.emit()

    # ── Subsystems config ─────────────────────────────────────────────────

    def load_config(self) -> dict[str, Any]:
        if not _SUBSYSTEMS.exists():
            shipped = paths.resource("assets", "cad", "subsystems.json")
            if shipped.exists():
                try:
                    return json.loads(shipped.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            return dict(_DEFAULT_CONFIG)
        try:
            return json.loads(_SUBSYSTEMS.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULT_CONFIG)

    def save_config(self, data: dict[str, Any]) -> None:
        _CAD_DIR.mkdir(parents=True, exist_ok=True)
        _SUBSYSTEMS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.config_changed.emit()

    # ── CAD active / subsystem focus ──────────────────────────────────────

    @property
    def cad_active(self) -> bool:
        return self._cad_active

    @property
    def focused_id(self) -> str:
        return self._focused_id

    def activate_cad(self, active: bool) -> None:
        if active == self._cad_active:
            return
        self._cad_active = active
        if not active:
            self._focused_id = ""
        self.cad_active_changed.emit(active)

    def focus_subsystem(self, sub_id: str) -> None:
        """Focus a subsystem on all connected viewers. sub_id='' = full view."""
        self._focused_id = sub_id
        if sub_id and not self._cad_active:
            self._cad_active = True
            self.cad_active_changed.emit(True)
        self.subsystem_focused.emit(sub_id)


cad_assets: _CADAssets = LazyProxy("cad_assets", "init_cad_assets")  # type: ignore[assignment]


def init_cad_assets() -> _CADAssets:
    """Call once in main() after init_config()."""
    real = _CADAssets()
    cad_assets._install(real)
    real.start_server()
    return real
