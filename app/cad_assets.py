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
import shutil
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

_ASSETS_DIR    = Path(__file__).parent.parent / "assets"
_CAD_DIR       = _ASSETS_DIR / "cad"
_SUBSYSTEMS    = _CAD_DIR / "subsystems.json"
_MODEL         = _CAD_DIR / "robot.glb"
_PORT          = 8765

_DEFAULT_CONFIG: dict[str, Any] = {
    "_doc": (
        "Season CAD config — edit each year. "
        "node_name must match the Onshape sub-assembly name exactly (case-sensitive)."
    ),
    "season": "YYYY",
    "subsystems": [],
}


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
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
        self._cad_active = False
        self._focused_id = ""

    # ── HTTP server ───────────────────────────────────────────────────────

    def start_server(self) -> None:
        if self._server is not None:
            return
        handler = functools.partial(_SilentHandler, directory=str(_ASSETS_DIR))
        self._server = _HTTPServer(("127.0.0.1", _PORT), handler)
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()

    # ── URLs ──────────────────────────────────────────────────────────────

    @property
    def viewer_url(self) -> str:
        return f"http://127.0.0.1:{_PORT}/cad_viewer/index.html"

    @property
    def port(self) -> int:
        return _PORT

    # ── Model file ────────────────────────────────────────────────────────

    @property
    def model_exists(self) -> bool:
        return _MODEL.exists()

    @property
    def model_path(self) -> Path:
        return _MODEL

    def import_model(self, source: Path) -> None:
        """Copy a .glb file from source into assets/cad/robot.glb."""
        _CAD_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, _MODEL)
        self.model_changed.emit()

    # ── Subsystems config ─────────────────────────────────────────────────

    def load_config(self) -> dict[str, Any]:
        if not _SUBSYSTEMS.exists():
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


# ── Lazy proxy (same pattern as config / rotation) ────────────────────────────

class _Proxy:
    _real: "_CADAssets | None" = None

    def __getattr__(self, name: str):
        if self._real is None:
            raise RuntimeError(
                f"cad_assets.{name} accessed before init_cad_assets() was called."
            )
        return getattr(self._real, name)

    def __setattr__(self, name: str, value):
        if name == "_real":
            object.__setattr__(self, name, value)
        else:
            setattr(self._real, name, value)


cad_assets: _CADAssets = _Proxy()  # type: ignore[assignment]


def init_cad_assets() -> _CADAssets:
    """Call once in main() after init_config()."""
    real = _CADAssets()
    cad_assets._real = real  # type: ignore[attr-defined]
    real.start_server()
    return real
