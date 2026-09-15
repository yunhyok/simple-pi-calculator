"""Synthetic plane / port placement preview (DESIGN.md §5.5 tab 3, §2.5).

Pure QPainter drawing of the plane W × H of the selected PWR with the PAD port (red square), the
decap ports (blue squares; ports carrying two capacitors get a double outline) and dimension
labels. No computation beyond the closed-form placement of §2.5.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from simple_pi_calculator.core.units import MM
from simple_pi_calculator.gui.engine_bridge import PreviewPlacement


class PlacementPreview(QWidget):
    """Draws a :class:`PreviewPlacement` (y = 0 edge, where the PAD sits, at the bottom)."""

    MARGIN = 36

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._placement: PreviewPlacement | None = None
        self._title = ""
        self._message = "Select a PWR net to preview its plane and ports."
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.paint_count = 0

    def sizeHint(self) -> QSize:
        return QSize(420, 240)

    @property
    def placement(self) -> PreviewPlacement | None:
        return self._placement

    def set_placement(self, placement: PreviewPlacement | None, title: str = "",
                      message: str = "") -> None:
        self._placement = placement
        self._title = title
        self._message = message or ("No valid geometry for this PWR (check width, layers and "
                                    "decap distances)." if placement is None else "")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.paint_count += 1
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.fillRect(self.rect(), QColor("#ffffff"))
            pl = self._placement
            if pl is None or pl.width_m <= 0 or pl.height_m <= 0:
                painter.setPen(QColor("#666666"))
                painter.drawText(self.rect().adjusted(8, 8, -8, -8),
                                 Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                 self._message)
                return
            self._paint_placement(painter, pl)
        finally:
            painter.end()

    def _paint_placement(self, painter: QPainter, pl: PreviewPlacement) -> None:
        m = self.MARGIN
        avail_w = max(self.width() - 2 * m, 10)
        avail_h = max(self.height() - 2 * m, 10)
        scale = min(avail_w / pl.width_m, avail_h / pl.height_m)
        pw, ph = pl.width_m * scale, pl.height_m * scale
        x0 = (self.width() - pw) / 2.0
        y0 = (self.height() - ph) / 2.0

        def to_px(x_m: float, y_m: float) -> QPointF:
            return QPointF(x0 + x_m * scale, y0 + ph - y_m * scale)

        plane = QRectF(x0, y0, pw, ph)
        painter.setPen(QPen(QColor("#8a5a00"), 1.5))
        painter.setBrush(QBrush(QColor("#f6e3c6")))
        painter.drawRect(plane)

        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 1, 7))
        painter.setFont(small)

        # D_ref guide: from PAD y to the farthest row
        pad_xy = pl.xy_m[0]
        guide_pen = QPen(QColor("#999999"), 1, Qt.PenStyle.DashLine)
        painter.setPen(guide_pen)
        y_far = pad_xy[1] + pl.d_ref_m
        painter.drawLine(to_px(0, y_far), to_px(pl.width_m, y_far))

        # ports
        for p in range(len(pl.xy_m)):
            x, y = pl.xy_m[p]
            w = float(pl.port_widths_m[p]) if p < len(pl.port_widths_m) else 0.0
            side = max(w * scale, 5.0)
            c = to_px(x, y)
            rect = QRectF(c.x() - side / 2, c.y() - side / 2, side, side)
            if p == 0:
                painter.setPen(QPen(QColor("#8b0000"), 1))
                painter.setBrush(QBrush(QColor("#d62728")))
                painter.drawRect(rect)
            else:
                caps = int(pl.caps_per_port[p - 1]) if p - 1 < len(pl.caps_per_port) else 1
                painter.setPen(QPen(QColor("#0b3d91"), 1))
                painter.setBrush(QBrush(QColor("#1f77b4")))
                painter.drawRect(rect)
                if caps >= 2:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor("#0b3d91"), 1))
                    painter.drawRect(rect.adjusted(-3, -3, 3, 3))

        # labels
        painter.setPen(QColor("#202020"))
        painter.drawText(QRectF(x0, y0 + ph + 4, pw, m - 6),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         f"W = {pl.width_m / MM:.2f} mm")
        painter.save()
        painter.translate(x0 - 6, y0 + ph / 2)
        painter.rotate(-90)
        painter.drawText(QRectF(-ph / 2, -m + 6, ph, m - 8),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                         f"H = {pl.height_m / MM:.2f} mm")
        painter.restore()
        pad_px = to_px(*pad_xy)
        painter.drawText(QRectF(pad_px.x() + 8, pad_px.y() - 16, 120, 14),
                         Qt.AlignmentFlag.AlignLeft, "PAD")
        header = self._title + ("  —  " if self._title else "") + \
            f"D_ref = {pl.d_ref_m / MM:.2f} mm, {pl.n_decap_ports} decap port(s)"
        painter.drawText(QRectF(4, 2, self.width() - 8, m - 6),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, header)


__all__ = ["PlacementPreview"]
