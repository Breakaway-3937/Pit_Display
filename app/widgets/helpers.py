"""Small shared Qt construction helpers used across control-panel widgets."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QFrame, QLabel, QLayout


def label(text: str, obj_name: str = "",
          align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(align)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl


class LinkLabel(QLabel):
    """
    Wrapped text with links that open in the browser. Qt draws rich-text
    links blue, and neither the stylesheet nor a later palette change reaches
    an anchor already laid out, so the colour goes into the anchor itself:
    the surface's primary ink (white on a dark ground, carbon on a light one,
    read from the label's own text colour), underlined. Never blue, never red
    (the brand puts no colour on type). For the data-source credits
    (`app/attribution.py`).
    """

    def __init__(self, html: str, obj_name: str = "stat_label"):
        super().__init__()
        self._html = html
        self._ink = ""
        self.setObjectName(obj_name)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.setWordWrap(True)
        self._render()

    def _render(self) -> None:
        from app import brand
        text = self.palette().color(QPalette.ColorRole.WindowText)
        ink = brand.WHITE if text.lightness() > 128 else brand.CARBON
        if ink == self._ink:
            return
        self._ink = ink
        self.setText(self._html.replace(
            "<a ", f'<a style="color:{ink}; text-decoration:underline;" '))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange):
            self._render()

    def showEvent(self, event):
        self.ensurePolished()
        self._render()
        super().showEvent(event)


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
    """
    Remove and delete every widget/item currently in the layout, **nested
    layouts included**. It used to delete only direct widgets, so a row built
    as a QHBoxLayout left its label and switch behind, unmanaged, piled at the
    parent's top-left (the Home Datasets ghosts, 2026-10-03). Widgets are
    hidden at once: deleteLater() alone leaves them painted until the loop
    gets to it.
    """
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
            item.layout().deleteLater()
