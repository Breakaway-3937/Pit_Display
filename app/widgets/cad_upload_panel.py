"""
CAD settings panel — shown in the control screen when 'CAD Viewer' is selected.

Provides:
  • Upload / replace robot.glb
  • Season year field
  • Subsystem list with add / edit / remove
  • Save / reload actions
"""

import json
import re
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from app import brand
from app.cad_assets import cad_assets
from app.config import config


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str, obj_name: str = "") -> QLabel:
    lbl = QLabel(text)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl


def _divider() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    return f


def _slug(text: str) -> str:
    """Turn a display name into a safe id string."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "subsystem"


# ── Subsystem edit dialog ─────────────────────────────────────────────────────

class _SubsystemDialog(QDialog):
    """
    Add / edit a single subsystem entry.

    Fields
    ------
    display_name  — label shown on screen (e.g. "Intake")
    node_name     — Onshape sub-assembly name, must match exactly
    accent_color  — hex color for the highlight / overlay accent
    facts         — one fact per line
    camera        — optional: azimuth_deg, elevation_deg, distance_factor
    """

    def __init__(self, parent=None, data: dict[str, Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit Subsystem" if data else "Add Subsystem")
        self.setMinimumWidth(480)
        self._build(data or {})

    def _build(self, d: dict[str, Any]):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name  = QLineEdit(d.get("display_name", ""))
        self._name.setPlaceholderText("e.g.  Intake")

        self._node  = QLineEdit(d.get("node_name", ""))
        self._node.setPlaceholderText("Exact Onshape sub-assembly name (case-sensitive)")

        self._color = QLineEdit(d.get("accent_color", brand.RED))
        self._color.setPlaceholderText("#RRGGBB")
        self._color.setMaximumWidth(120)

        self._facts = QTextEdit()
        self._facts.setPlaceholderText("One fact per line…")
        self._facts.setFixedHeight(120)
        self._facts.setPlainText("\n".join(d.get("facts", [])))

        form.addRow("Display name:", self._name)
        form.addRow("Onshape node name:", self._node)
        form.addRow("Accent color:", self._color)
        form.addRow("Facts:", self._facts)

        layout.addLayout(form)

        # Camera preset (collapsible)
        self._cam_box = QCheckBox("Custom camera preset")
        layout.addWidget(self._cam_box)

        self._cam_widget = QWidget()
        cam_form = QFormLayout(self._cam_widget)
        cam_form.setSpacing(8)
        preset = d.get("camera", {})
        self._az  = QLineEdit(str(preset.get("azimuth_deg",  45)))
        self._el  = QLineEdit(str(preset.get("elevation_deg", 25)))
        self._df  = QLineEdit(str(preset.get("distance_factor", 2.2)))
        cam_form.addRow("Azimuth (°):",       self._az)
        cam_form.addRow("Elevation (°):",     self._el)
        cam_form.addRow("Distance factor:",   self._df)
        hint = _lbl(
            "Azimuth/elevation rotate the camera around the sub-assembly.\n"
            "Distance factor × sub-assembly size = camera distance.",
            "stat_label",
        )
        hint.setWordWrap(True)
        cam_form.addRow("", hint)
        self._cam_widget.setVisible(bool(preset))
        self._cam_box.setChecked(bool(preset))
        self._cam_box.toggled.connect(self._cam_widget.setVisible)
        layout.addWidget(self._cam_widget)

        layout.addWidget(_divider())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, "Missing field", "Display name is required.")
            return
        if not self._node.text().strip():
            QMessageBox.warning(self, "Missing field", "Onshape node name is required.")
            return
        self.accept()

    def result_data(self, existing_id: str = "") -> dict[str, Any]:
        facts = [f for f in self._facts.toPlainText().splitlines() if f.strip()]
        d: dict[str, Any] = {
            "id":           existing_id or _slug(self._name.text().strip()),
            "display_name": self._name.text().strip(),
            "node_name":    self._node.text().strip(),
            "accent_color": self._color.text().strip() or brand.RED,
            "facts":        facts,
        }
        if self._cam_box.isChecked():
            def _f(w, default):
                try: return float(w.text())
                except ValueError: return default
            d["camera"] = {
                "azimuth_deg":    _f(self._az, 45),
                "elevation_deg":  _f(self._el, 25),
                "distance_factor": _f(self._df, 2.2),
            }
        return d


# ── Subsystem row (in the list) ───────────────────────────────────────────────

class _SubRow(QFrame):
    edit_clicked   = pyqtSignal(int)
    delete_clicked = pyqtSignal(int)

    def __init__(self, index: int, sub: dict[str, Any]):
        super().__init__()
        self._index = index
        self.setFrameShape(QFrame.Shape.StyledPanel)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        color_dot = QLabel("●")
        color_dot.setStyleSheet(f"color: {sub.get('accent_color', '#888888')}; font-size: 14px;")
        row.addWidget(color_dot)

        name_lbl = _lbl(sub.get("display_name", "?"), "stat_value")
        node_lbl = _lbl(f"node: {sub.get('node_name', '?')}", "stat_label")
        node_lbl.setStyleSheet("font-family: Roboto; font-size: 11px;")

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.addWidget(name_lbl)
        text_col.addWidget(node_lbl)
        row.addLayout(text_col, stretch=1)

        facts_count = len(sub.get("facts", []))
        row.addWidget(_lbl(f"{facts_count} fact{'s' if facts_count != 1 else ''}", "stat_label"))

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedWidth(56)
        edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self._index))
        row.addWidget(edit_btn)

        del_btn = QPushButton("✕")
        del_btn.setFixedWidth(30)
        del_btn.setObjectName("btn_danger")
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self._index))
        row.addWidget(del_btn)


# ── CAD settings panel ────────────────────────────────────────────────────────

class CADSettingsPanel(QWidget):
    """
    Full settings panel shown in the control screen when the CAD Viewer
    screen card is selected.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subsystems: list[dict[str, Any]] = []
        self._build()
        self._load()
        cad_assets.config_changed.connect(self._load)
        cad_assets.model_changed.connect(self._refresh_model_status)
        config.team_changed.connect(self._on_team_changed)

    # ── Build UI ──────────────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(6)

        outer.addWidget(_lbl("CAD VIEWER", "section_header"))
        outer.addWidget(_lbl("Manage the 3D robot model and subsystem definitions.", "stat_label"))
        outer.addSpacing(12)
        outer.addWidget(_divider())
        outer.addSpacing(10)

        # ── Model section ────────────────────────────────────────────────
        outer.addWidget(_lbl("Robot Model", "screen_title"))
        outer.addSpacing(6)

        model_row = QHBoxLayout()
        model_row.setSpacing(10)
        self._model_status = _lbl("No model uploaded", "stat_label")
        self._model_status.setWordWrap(True)
        model_row.addWidget(self._model_status, stretch=1)
        upload_btn = QPushButton("Upload Model (.glb) …")
        upload_btn.setObjectName("btn_primary")
        upload_btn.clicked.connect(self._upload_model)
        model_row.addWidget(upload_btn)
        outer.addLayout(model_row)

        hint = _lbl(
            "Export from Onshape: right-click assembly → Export → GLTF/GLB (single file, preserve hierarchy).",
            "stat_label",
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)
        outer.addSpacing(12)
        outer.addWidget(_divider())
        outer.addSpacing(10)

        # ── Season ───────────────────────────────────────────────────────
        outer.addWidget(_lbl("Season", "screen_title"))
        outer.addSpacing(6)

        season_row = QHBoxLayout()
        season_row.setSpacing(10)
        season_row.addWidget(_lbl("Season year:", "stat_label"))
        self._season_edit = QLineEdit()
        self._season_edit.setMaximumWidth(100)
        self._season_edit.setPlaceholderText("2025")
        season_row.addWidget(self._season_edit)
        season_row.addStretch()
        outer.addLayout(season_row)
        outer.addSpacing(12)
        outer.addWidget(_divider())
        outer.addSpacing(10)

        # ── Subsystems ───────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.addWidget(_lbl("Subsystems", "screen_title"))
        header_row.addStretch()
        add_btn = QPushButton("+ Add Subsystem")
        add_btn.setObjectName("btn_primary")
        add_btn.clicked.connect(self._add_subsystem)
        header_row.addWidget(add_btn)
        outer.addLayout(header_row)
        outer.addSpacing(6)

        setup_hint = _lbl(
            "Run  uv run scripts/setup_cad_assets.py  once to download Three.js / GSAP offline libs.",
            "stat_label",
        )
        setup_hint.setStyleSheet("font-family: Roboto; font-size: 11px;")
        setup_hint.setWordWrap(True)
        outer.addWidget(setup_hint)
        outer.addSpacing(6)

        self._sub_scroll = QScrollArea()
        self._sub_scroll.setWidgetResizable(True)
        self._sub_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._sub_scroll.setMinimumHeight(160)
        self._sub_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._sub_list_widget = QWidget()
        self._sub_list_layout = QVBoxLayout(self._sub_list_widget)
        self._sub_list_layout.setContentsMargins(0, 0, 0, 0)
        self._sub_list_layout.setSpacing(4)
        self._sub_scroll.setWidget(self._sub_list_widget)
        outer.addWidget(self._sub_scroll, stretch=1)

        outer.addSpacing(10)

        # ── Save row ─────────────────────────────────────────────────────
        save_row = QHBoxLayout()
        save_row.setSpacing(8)
        self._status_lbl = _lbl("", "stat_label")
        save_row.addWidget(self._status_lbl, stretch=1)
        reload_btn = QPushButton("↺  Reload Viewer")
        reload_btn.clicked.connect(self._reload_viewer)
        save_row.addWidget(reload_btn)
        save_btn = QPushButton("Save Config")
        save_btn.setObjectName("btn_primary")
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        outer.addLayout(save_row)

    # ── Load / refresh ────────────────────────────────────────────────────

    def _load(self):
        data = cad_assets.load_config()
        self._subsystems = list(data.get("subsystems", []))
        self._season_edit.setText(str(data.get("season", "")))
        self._refresh_sub_list()
        self._refresh_model_status()

    def _refresh_model_status(self):
        if cad_assets.model_exists:
            p = cad_assets.model_path
            sz = p.stat().st_size // 1024
            self._model_status.setText(f"✓  robot.glb  ({sz} KB)")
        else:
            self._model_status.setText("No model uploaded")

    def _refresh_sub_list(self):
        while self._sub_list_layout.count():
            item = self._sub_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._subsystems:
            self._sub_list_layout.addWidget(
                _lbl("No subsystems yet — click '+ Add Subsystem' to define one.", "stat_label")
            )
        else:
            for i, sub in enumerate(self._subsystems):
                row = _SubRow(i, sub)
                row.edit_clicked.connect(self._edit_subsystem)
                row.delete_clicked.connect(self._delete_subsystem)
                self._sub_list_layout.addWidget(row)

        self._sub_list_layout.addStretch()

    # ── Model upload ──────────────────────────────────────────────────────

    def _upload_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Robot Model", "", "GLTF Binary (*.glb);;All Files (*)"
        )
        if not path:
            return
        try:
            cad_assets.import_model(Path(path))
            self._set_status("Model uploaded — click Reload Viewer to apply.")
        except Exception as e:
            QMessageBox.critical(self, "Upload failed", str(e))

    # ── Subsystem CRUD ────────────────────────────────────────────────────

    def _add_subsystem(self):
        dlg = _SubsystemDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_sub = dlg.result_data()
            # Ensure unique id
            existing_ids = {s["id"] for s in self._subsystems}
            base = new_sub["id"]
            ctr = 1
            while new_sub["id"] in existing_ids:
                new_sub["id"] = f"{base}_{ctr}"
                ctr += 1
            self._subsystems.append(new_sub)
            self._refresh_sub_list()
            self._set_status("Subsystem added — remember to Save Config.")

    def _edit_subsystem(self, index: int):
        sub = self._subsystems[index]
        dlg = _SubsystemDialog(self, data=sub)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._subsystems[index] = dlg.result_data(existing_id=sub["id"])
            self._refresh_sub_list()
            self._set_status("Subsystem updated — remember to Save Config.")

    def _delete_subsystem(self, index: int):
        name = self._subsystems[index].get("display_name", "this subsystem")
        reply = QMessageBox.question(
            self, "Delete subsystem",
            f"Remove '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._subsystems.pop(index)
            self._refresh_sub_list()
            self._set_status("Subsystem removed — remember to Save Config.")

    # ── Save / reload ─────────────────────────────────────────────────────

    def _save(self):
        existing = cad_assets.load_config()
        existing["season"]     = self._season_edit.text().strip() or "YYYY"
        existing["subsystems"] = self._subsystems
        cad_assets.save_config(existing)
        self._set_status("Config saved.")

    def _reload_viewer(self):
        from app.cad_assets import cad_assets as _ca
        _ca.model_changed.emit()
        self._set_status("Reload signal sent to viewers.")

    def _set_status(self, msg: str):
        self._status_lbl.setText(msg)

    def _on_team_changed(self, _team):
        pass
