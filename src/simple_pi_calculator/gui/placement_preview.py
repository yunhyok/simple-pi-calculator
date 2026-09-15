"""Synthetic plane / port placement preview (DESIGN.md §5.5 tab 3, §2.5).

Pure QPainter drawing of the plane W × H of the selected PWR with the N_pad observation pad ports
(red squares), the decap ports (blue squares; ports carrying two capacitors get a double outline)
and dimension labels. No computation beyond the closed-form placement of §2.5. With a sampled
distance distribution (§2.5.5) the decap ports are scattered in y; hovering a port shows its row,
via-set index and distance as a tooltip (:meth:`PlacementPreview.tooltip_text`).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from simple_pi_calculator.constants import PAD_MARGIN_FACTOR
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

    def _frame(self, pl: PreviewPlacement) -> tuple[float, float, float, float, float]:
        """(x0, y0, scale, plane width px, plane height px) of the drawing."""
        m = self.MARGIN
        avail_w = max(self.width() - 2 * m, 10)
        avail_h = max(self.height() - 2 * m, 10)
        scale = min(avail_w / pl.width_m, avail_h / pl.height_m)
        pw, ph = pl.width_m * scale, pl.height_m * scale
        return (self.width() - pw) / 2.0, (self.height() - ph) / 2.0, scale, pw, ph

    def port_at(self, pos: QPointF) -> int | None:
        """Index of the port drawn under widget position ``pos`` (nearest centre), or ``None``."""
        pl = self._placement
        if pl is None or pl.width_m <= 0 or pl.height_m <= 0 or len(pl.xy_m) == 0:
            return None
        x0, y0, scale, _pw, ph = self._frame(pl)
        best, best_d = None, None
        for p in range(len(pl.xy_m)):
            w = float(pl.port_widths_m[p]) if p < len(pl.port_widths_m) else 0.0
            half = max(w * scale, 5.0) / 2.0 + 3.0
            cx = x0 + float(pl.xy_m[p][0]) * scale
            cy = y0 + ph - float(pl.xy_m[p][1]) * scale
            dx, dy = abs(pos.x() - cx), abs(pos.y() - cy)
            if dx <= half and dy <= half and (best_d is None or dx * dx + dy * dy < best_d):
                best, best_d = p, dx * dx + dy * dy
        return best

    def tooltip_text(self, pos: QPointF) -> str:
        """Tooltip of the port under ``pos``: PAD index, or decap row, via set and distance."""
        pl = self._placement
        p = self.port_at(pos)
        if pl is None or p is None:
            return ""
        n_pads = max(1, int(getattr(pl, "n_pads", 1)))
        x_mm, y_mm = (float(v) / MM for v in pl.xy_m[p])
        if p < n_pads:
            return f"PAD {p + 1}: x = {x_mm:.3f} mm, y = {y_mm:.3f} mm"
        j = p - n_pads
        k = int(pl.group_index[j]) if j < len(pl.group_index) else 0
        within = sum(1 for g in pl.group_index[:j] if int(g) == k)
        labels = getattr(pl, "row_labels", None) or []
        label = labels[k] if k < len(labels) else f"row {k + 1}"
        caps = int(pl.caps_per_port[j]) if j < len(pl.caps_per_port) else 1
        dists = getattr(pl, "port_distances_m", None)
        d_text = "" if dists is None or j >= len(dists) else \
            f"\ndistance to PAD row = {float(dists[j]) / MM:.4f} mm" + \
            (" (sampled)" if getattr(pl, "distance_mode", "fixed") == "normal" else "")
        return (f"Decap {label}, via set {within + 1} ({caps} capacitor{'s' if caps > 1 else ''})"
                f"{d_text}\nx = {x_mm:.3f} mm, y = {y_mm:.3f} mm")

    def event(self, ev) -> bool:  # noqa: N802 - Qt API
        if ev.type() == QEvent.Type.ToolTip:
            text = self.tooltip_text(QPointF(ev.pos()))
            if text:
                QToolTip.showText(ev.globalPos(), text, self)
            else:
                QToolTip.hideText()
                ev.ignore()
            return True
        return super().event(ev)

    def _paint_placement(self, painter: QPainter, pl: PreviewPlacement) -> None:
        m = self.MARGIN
        x0, y0, scale, pw, ph = self._frame(pl)

        def to_px(x_m: float, y_m: float) -> QPointF:
            return QPointF(x0 + x_m * scale, y0 + ph - y_m * scale)

        plane = QRectF(x0, y0, pw, ph)
        painter.setPen(QPen(QColor("#8a5a00"), 1.5))
        painter.setBrush(QBrush(QColor("#f6e3c6")))
        painter.drawRect(plane)

        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 1, 7))
        painter.setFont(small)

        n_pads = max(1, int(getattr(pl, "n_pads", 1)))
        # D_ref guide: from the pad row (y = 0.2·D_ref) to the farthest row
        guide_pen = QPen(QColor("#999999"), 1, Qt.PenStyle.DashLine)
        painter.setPen(guide_pen)
        y_far = PAD_MARGIN_FACTOR * pl.d_ref_m + pl.d_ref_m
        painter.drawLine(to_px(0, y_far), to_px(pl.width_m, y_far))

        # ports
        for p in range(len(pl.xy_m)):
            x, y = pl.xy_m[p]
            w = float(pl.port_widths_m[p]) if p < len(pl.port_widths_m) else 0.0
            side = max(w * scale, 5.0)
            c = to_px(x, y)
            rect = QRectF(c.x() - side / 2, c.y() - side / 2, side, side)
            if p < n_pads:
                painter.setPen(QPen(QColor("#8b0000"), 1))
                painter.setBrush(QBrush(QColor("#d62728")))
                painter.drawRect(rect)
            else:
                j = p - n_pads
                caps = int(pl.caps_per_port[j]) if j < len(pl.caps_per_port) else 1
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
        pad_label = "PAD" if n_pads == 1 else f"{n_pads} PADs"
        last_pad = pl.xy_m[n_pads - 1]
        pad_px = to_px(float(last_pad[0]), float(last_pad[1]))
        painter.setPen(QColor("#8b0000"))
        painter.drawText(QRectF(pad_px.x() + 8, pad_px.y() - 16, 120, 14),
                         Qt.AlignmentFlag.AlignLeft, pad_label)
        painter.setPen(QColor("#202020"))
        header = self._title + ("  —  " if self._title else "") + \
            f"D_ref = {pl.d_ref_m / MM:.2f} mm, N_pad = {n_pads}, " \
            f"{pl.n_decap_ports} decap port(s)"
        if getattr(pl, "distance_mode", "fixed") == "normal":
            header += f", normal ±1σ: σ {pl.sigma_mm:g} mm, seed {pl.seed}"
        painter.drawText(QRectF(4, 2, self.width() - 8, m - 6),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, header)


__all__ = ["PlacementPreview"]
