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
    """
    The section rule in the settings column — 2px, not a hairline.

    The column is a ruled list: 1px `#262227` closes a *row*, 2px closes a
    *section*. Two weights is what lets an operator see the grouping at a
    glance instead of reading every label.
    """
    from app import brand

    line = QFrame()
    line.setObjectName("section_rule")
    line.setFixedHeight(2)
    line.setStyleSheet(
        f"QFrame#section_rule {{ background-color: {brand.CARBON_LINE};"
        f" border: none; }}")
    return line


def clear_layout(layout: QLayout) -> None:
    """Remove and delete every widget/item currently in the layout."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
