"""Small shared Qt construction helpers used across control-panel widgets."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QLayout


def label(text: str, obj_name: str = "",
          align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(align)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl


def divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    return line


def clear_layout(layout: QLayout) -> None:
    """Remove and delete every widget/item currently in the layout."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
