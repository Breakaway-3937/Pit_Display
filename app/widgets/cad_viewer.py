"""QWebEngineView wrapper for the Three.js CAD viewer."""

import json

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView

from app.cad_assets import cad_assets


class _Page(QWebEnginePage):
    """Intercepts structured console.log messages from viewer.js."""

    subsystem_selected = pyqtSignal(str)
    view_reset         = pyqtSignal()

    def javaScriptConsoleMessage(self, level, message, line, source):
        try:
            data = json.loads(message)
            t = data.get("type")
            if t == "subsystem_selected":
                self.subsystem_selected.emit(data["id"])
            elif t == "view_reset":
                self.view_reset.emit()
        except (json.JSONDecodeError, KeyError, TypeError):
            pass


class CADViewerWidget(QWebEngineView):
    """
    Embeds the Three.js CAD viewer served by the local HTTP server.

    Python → JS:  focus_subsystem(), reset_view(), set_mode(), reload_model()
    JS → Python:  subsystem_selected signal, view_reset signal
    """

    subsystem_selected = pyqtSignal(str)
    view_reset         = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        page = _Page(self)
        page.subsystem_selected.connect(self.subsystem_selected)
        page.view_reset.connect(self.view_reset)
        self.setPage(page)
        self.setUrl(QUrl(cad_assets.viewer_url))

    # ── Python → JS ───────────────────────────────────────────────────────

    def focus_subsystem(self, sub_id: str) -> None:
        self._js(f"window.cadViewer && window.cadViewer.focusSubsystem({json.dumps(sub_id)})")

    def reset_view(self) -> None:
        self._js("window.cadViewer && window.cadViewer.resetView()")

    def set_mode(self, mode: str) -> None:
        """mode: 'interactive' | 'judges'"""
        self._js(f"window.cadViewer && window.cadViewer.setMode({json.dumps(mode)})")

    def reload_model(self) -> None:
        self._js("window.cadViewer && window.cadViewer.reload()")

    def _js(self, script: str) -> None:
        self.page().runJavaScript(script)
