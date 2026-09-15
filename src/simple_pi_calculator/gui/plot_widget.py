"""Impedance plot based on pyqtgraph (DESIGN.md §5.6).

``ImpedancePlot`` shows one or more PWR curves in log-log coordinates. In log mode pyqtgraph
takes data in linear units but the ViewBox coordinates are log10 — marker lines, text positions
and ranges therefore use log10 values (§5.6 pitfall).
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters  # noqa: F401 - explicit import so PyInstaller collects it (§5.6)
from PySide6.QtCore import Qt, Signal

from simple_pi_calculator.constants import MARKER_FREQUENCIES_HZ, Z_PLOT_FLOOR_OHM
from simple_pi_calculator.core.units import format_frequency, format_sig, z_label, z_scale
from simple_pi_calculator.io.project_io import PlotView

pg.setConfigOptions(antialias=True, background="w", foreground="k")

PALETTE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf",
           "#bcbd22", "#7f7f7f", "#d62728")
MARKER_COLOR = "#d62728"


def series_color(index: int) -> str:
    return PALETTE[index % len(PALETTE)]


def engineering_frequency(f_hz: float) -> str:
    """Tick text ``100k``, ``1M``, ``2.5G`` …"""
    for scale, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, "")):
        if abs(f_hz) >= scale * 0.9999:
            return f"{f_hz / scale:.3g}{suffix}"
    return f"{f_hz:.3g}"


class LogFreqAxis(pg.AxisItem):
    """Bottom axis for log-mode frequency: tick values are log10(f) (§5.6)."""

    def tickStrings(self, values: Sequence[float], scale: float,
                    spacing: float | None) -> list[str]:
        # Called from paint(): an exception here would abort the paint event, so be defensive.
        try:
            values = [float(v) for v in values]
            if not self.logMode:
                return [engineering_frequency(v) for v in values]
            # pyqtgraph passes spacing=None for the minor (2…9 ×) log ticks
            minor = spacing is None or spacing < 1.0
            span = (max(values) - min(values)) if len(values) > 1 else 0.0
            out = []
            for v in values:
                is_decade = abs(v - round(v)) < 1e-6
                if minor and not is_decade and span >= 1.0:
                    # over more than a decade label only the 2× and 5× minor ticks
                    mant = 10 ** (v - math.floor(v))
                    if not (abs(mant - 2) < 1e-3 or abs(mant - 5) < 1e-3):
                        out.append("")
                        continue
                out.append(engineering_frequency(10 ** v))
            return out
        except Exception:  # noqa: BLE001
            return ["" for _ in values]


class _Series:
    """One PWR on a plot."""

    def __init__(self, name: str, f_hz: np.ndarray, z: np.ndarray, z_plane: np.ndarray | None,
                 marker_f: np.ndarray, marker_z: np.ndarray, color: str):
        self.name = name
        self.f_hz = np.asarray(f_hz, dtype=float)
        self.abs_z = np.abs(np.asarray(z, dtype=complex))
        self.abs_plane = None if z_plane is None else np.abs(np.asarray(z_plane, dtype=complex))
        self.marker_f = np.asarray(marker_f, dtype=float)
        self.marker_abs = np.abs(np.asarray(marker_z, dtype=complex))
        self.color = color
        self.curve: pg.PlotDataItem | None = None
        self.plane_curve: pg.PlotDataItem | None = None
        self.marker_dots: pg.PlotDataItem | None = None
        self.marker_texts: list[pg.TextItem] = []
        self.visible = True

    def marker_value(self, f: float) -> float | None:
        """|Z| in Ω at marker frequency ``f`` (exact from the result, else interpolated)."""
        for mf, mz in zip(self.marker_f, self.marker_abs):
            if abs(mf - f) <= 1e-9 * max(abs(f), 1.0):
                return float(mz)
        if len(self.f_hz) >= 2 and self.f_hz[0] <= f <= self.f_hz[-1]:
            return float(10 ** np.interp(math.log10(f), np.log10(self.f_hz),
                                         np.log10(np.maximum(self.abs_z, Z_PLOT_FLOOR_OHM))))
        return None


class ImpedancePlot(pg.GraphicsLayoutWidget):
    """Log-log |Z(f)| plot with markers, hover readout, unit switching and view state (§5.6)."""

    viewChanged = Signal()

    def __init__(self, parent=None, title: str = "", unit: str = "mohm",
                 marker_frequencies: Sequence[float] = MARKER_FREQUENCIES_HZ,
                 marker_texts: bool = True):
        super().__init__(parent)
        self._title = title
        self._stale = False
        self._unit = unit
        self._marker_freqs = tuple(float(f) for f in marker_frequencies)
        self._marker_texts_enabled = marker_texts
        self._show_plane_only = False
        self._show_markers = True
        self._series: dict[str, _Series] = {}
        self._programmatic = 0

        self.readout = pg.LabelItem(justify="left")
        self.readout.setText(" ")
        self.addItem(self.readout, row=0, col=0)
        self.plot: pg.PlotItem = self.addPlot(row=1, col=0,
                                              axisItems={"bottom": LogFreqAxis("bottom")})
        self.plot.setLogMode(x=True, y=True)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("bottom", "Frequency", units=None)
        self.plot.getAxis("bottom").setLabel("Frequency (Hz)")
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.legend = self.plot.addLegend(offset=(-10, 10))
        self.vb: pg.ViewBox = self.plot.getViewBox()
        self.vb.setMouseMode(pg.ViewBox.PanMode)
        self.marker_lines: list[pg.InfiniteLine] = []
        for f in self._marker_freqs:
            line = pg.InfiniteLine(pos=math.log10(f), angle=90, movable=False,
                                   pen=pg.mkPen(MARKER_COLOR, width=1, style=Qt.PenStyle.DashLine),
                                   label=format_frequency(f, 3).replace(".0 ", " "),
                                   labelOpts={"position": 0.95, "color": MARKER_COLOR})
            line.marker_frequency_hz = f
            self.plot.addItem(line, ignoreBounds=True)
            self.marker_lines.append(line)
        self.cross_v = pg.InfiniteLine(angle=90, movable=False,
                                       pen=pg.mkPen("#888888", width=1, style=Qt.PenStyle.DotLine))
        self.cross_h = pg.InfiniteLine(angle=0, movable=False,
                                       pen=pg.mkPen("#888888", width=1, style=Qt.PenStyle.DotLine))
        for line in (self.cross_v, self.cross_h):
            line.setVisible(False)
            self.plot.addItem(line, ignoreBounds=True)
        self._update_axis_label()
        self._update_title()
        self.vb.sigRangeChangedManually.connect(self._on_manual_range)
        self.vb.sigStateChanged.connect(self._on_state_changed)
        self._mouse_proxy = pg.SignalProxy(self.scene().sigMouseMoved, rateLimit=30,
                                           slot=self._on_move)
        self._last_auto = tuple(self.vb.autoRangeEnabled())
        self.vb.setRange(xRange=(5, 9), yRange=(-1, 3), padding=0)

    # -- data -------------------------------------------------------------------------------------
    @property
    def unit(self) -> str:
        return self._unit

    @property
    def scale(self) -> float:
        return z_scale(self._unit)

    def series_names(self) -> list[str]:
        return list(self._series)

    def curve(self, name: str) -> pg.PlotDataItem | None:
        s = self._series.get(name)
        return None if s is None else s.curve

    def plane_curve(self, name: str) -> pg.PlotDataItem | None:
        s = self._series.get(name)
        return None if s is None else s.plane_curve

    def clear_results(self) -> None:
        for s in self._series.values():
            for item in (s.curve, s.plane_curve, s.marker_dots):
                if item is not None:
                    self.plot.removeItem(item)
            for t in s.marker_texts:
                self.plot.removeItem(t)
        self._series.clear()
        self.legend.clear()
        self.readout.setText(" ")

    def set_results(self, results: Sequence[Any], colors: Sequence[str] | None = None) -> None:
        """Show ``PwrResult``-like objects (``name``, ``f_hz``, ``z_pad``, ``z_plane_only``,
        ``marker_f_hz``, ``marker_z``)."""
        self._programmatic += 1
        try:
            self.clear_results()
            f_min, f_max = math.inf, 0.0
            for i, res in enumerate(results):
                color = colors[i] if colors is not None and i < len(colors) else series_color(i)
                marker_f = getattr(res, "marker_f_hz", None)
                marker_z = getattr(res, "marker_z", None)
                s = _Series(res.name, res.f_hz, res.z_pad, getattr(res, "z_plane_only", None),
                            np.asarray([] if marker_f is None else marker_f),
                            np.asarray([] if marker_z is None else marker_z), color)
                if len(s.f_hz) == 0:
                    continue
                f_min = min(f_min, float(s.f_hz[0]))
                f_max = max(f_max, float(s.f_hz[-1]))
                s.curve = self.plot.plot(s.f_hz, self._scaled(s.abs_z),
                                         pen=pg.mkPen(color, width=2), name=res.name)
                if s.abs_plane is not None:
                    plane_color = "#7f7f7f" if len(results) == 1 else color
                    s.plane_curve = self.plot.plot(
                        s.f_hz, self._scaled(s.abs_plane),
                        pen=pg.mkPen(plane_color, width=1, style=Qt.PenStyle.DashLine),
                        name=f"{res.name} plane only")
                    s.plane_curve.setVisible(bool(self._show_plane_only))
                    if not self._show_plane_only:
                        self._legend_remove(s.plane_curve)
                s.marker_dots = pg.PlotDataItem([], [], pen=None, symbol="o", symbolSize=7,
                                                symbolBrush=pg.mkBrush(color),
                                                symbolPen=pg.mkPen(MARKER_COLOR))
                self.plot.addItem(s.marker_dots)
                if self._marker_texts_enabled:
                    for _ in self._marker_freqs:
                        text = pg.TextItem("", color=color, anchor=(0, 1))
                        self.plot.addItem(text, ignoreBounds=True)
                        s.marker_texts.append(text)
                self._series[res.name] = s
            if f_max > 0 and math.isfinite(f_min):
                self.vb.setLimits(xMin=math.log10(f_min) - 0.5, xMax=math.log10(f_max) + 0.5)
                for line in self.marker_lines:
                    f = line.marker_frequency_hz
                    line.setVisible(bool(self._show_markers and f_min <= f <= f_max))
            self._update_markers()
            self.vb.enableAutoRange()
            self.vb.autoRange()
        finally:
            self._programmatic -= 1

    def _legend_remove(self, item: pg.PlotDataItem) -> None:
        try:
            self.legend.removeItem(item)
        except Exception:  # noqa: BLE001
            pass

    def _scaled(self, abs_z: np.ndarray) -> np.ndarray:
        return np.maximum(abs_z, Z_PLOT_FLOOR_OHM) * self.scale

    def _update_markers(self) -> None:
        for s in self._series.values():
            xs, ys = [], []
            for k, f in enumerate(self._marker_freqs):
                val = s.marker_value(f)
                text_item = s.marker_texts[k] if k < len(s.marker_texts) else None
                if val is None:
                    if text_item is not None:
                        text_item.setVisible(False)
                    continue
                scaled = max(val, Z_PLOT_FLOOR_OHM) * self.scale
                xs.append(f)
                ys.append(scaled)
                if text_item is not None:
                    text_item.setText(f"|Z| = {format_sig(scaled, 4)} {z_label(self._unit)}")
                    text_item.setPos(math.log10(f), math.log10(scaled))
                    text_item.setVisible(bool(self._show_markers and s.visible))
            if s.marker_dots is not None:
                s.marker_dots.setData(xs, ys)
                s.marker_dots.setVisible(bool(self._show_markers and s.visible))

    # -- display options --------------------------------------------------------------------------
    def set_unit(self, unit: str) -> None:
        """Rescale without recompute; X range kept, Y range shifted by log10(s_new/s_old)."""
        if unit == self._unit:
            return
        old = self.scale
        self._unit = unit
        new = self.scale
        self._programmatic += 1
        try:
            auto_y = bool(self.vb.autoRangeEnabled()[1])
            (x0, x1), (y0, y1) = self.vb.viewRange()
            for s in self._series.values():
                if s.curve is not None:
                    s.curve.setData(s.f_hz, self._scaled(s.abs_z))
                if s.plane_curve is not None and s.abs_plane is not None:
                    s.plane_curve.setData(s.f_hz, self._scaled(s.abs_plane))
            self._update_markers()
            self._update_axis_label()
            if not auto_y:
                shift = math.log10(new / old)
                self.vb.setXRange(x0, x1, padding=0)
                self.vb.setYRange(y0 + shift, y1 + shift, padding=0)
        finally:
            self._programmatic -= 1
        self.viewChanged.emit()

    def set_plane_only_visible(self, visible: bool) -> None:
        self._show_plane_only = bool(visible)
        for s in self._series.values():
            if s.plane_curve is None:
                continue
            show = self._show_plane_only and s.visible
            s.plane_curve.setVisible(bool(show))
            self._legend_remove(s.plane_curve)
            if self._show_plane_only:
                self.legend.addItem(s.plane_curve, f"{s.name} plane only")

    def set_markers_visible(self, visible: bool) -> None:
        self._show_markers = bool(visible)
        f_lo = float(min((s.f_hz[0] for s in self._series.values()), default=0.0))
        f_hi = float(max((s.f_hz[-1] for s in self._series.values()), default=math.inf))
        for line in self.marker_lines:
            f = line.marker_frequency_hz
            line.setVisible(bool(self._show_markers and f_lo <= f <= f_hi))
        self._update_markers()

    def markers_visible(self) -> bool:
        return self._show_markers

    def set_curve_visible(self, name: str, visible: bool) -> None:
        s = self._series.get(name)
        if s is None:
            return
        s.visible = bool(visible)
        if s.curve is not None:
            s.curve.setVisible(bool(s.visible))
        if s.plane_curve is not None:
            s.plane_curve.setVisible(bool(s.visible and self._show_plane_only))
        self._update_markers()

    def is_curve_visible(self, name: str) -> bool:
        s = self._series.get(name)
        return bool(s and s.curve is not None and s.curve.isVisible())

    def set_title(self, title: str) -> None:
        self._title = title
        self._update_title()

    def set_stale(self, stale: bool) -> None:
        self._stale = bool(stale)
        self._update_title()

    def title_text(self) -> str:
        return self._title + (" (inputs changed)" if self._stale else "")

    def _update_title(self) -> None:
        text = self.title_text()
        self.plot.setTitle(text if text else None)

    def axis_label_text(self) -> str:
        return f"|Z| ({z_label(self._unit)})"

    def _update_axis_label(self) -> None:
        self.plot.getAxis("left").setLabel(self.axis_label_text())

    # -- view state -------------------------------------------------------------------------------
    def reset_zoom(self) -> None:
        self.plot.enableAutoRange()
        self.plot.autoRange()
        self.viewChanged.emit()

    def view_state(self) -> PlotView:
        auto = self.vb.autoRangeEnabled()
        (x0, x1), (y0, y1) = self.vb.viewRange()
        return PlotView(auto_range=bool(auto[0] and auto[1]),
                        x_range_log10=(float(x0), float(x1)),
                        y_range_log10=(float(y0), float(y1)))

    def apply_view_state(self, view: PlotView) -> None:
        self._programmatic += 1
        try:
            if view.auto_range or view.x_range_log10 is None or view.y_range_log10 is None:
                self.vb.enableAutoRange()
                self.vb.autoRange()
            else:
                self.vb.disableAutoRange()
                self.vb.setRange(xRange=view.x_range_log10, yRange=view.y_range_log10,
                                 padding=0)
        finally:
            self._programmatic -= 1

    def _on_manual_range(self, *_args) -> None:
        if not self._programmatic:
            self.viewChanged.emit()

    def _on_state_changed(self, *_args) -> None:
        auto = tuple(self.vb.autoRangeEnabled())
        if auto != self._last_auto:
            self._last_auto = auto
            if not self._programmatic:
                self.viewChanged.emit()

    # -- hover readout ----------------------------------------------------------------------------
    def _on_move(self, event: tuple) -> None:
        pos = event[0]
        if not self.plot.sceneBoundingRect().contains(pos) or not self._series:
            self.cross_v.setVisible(False)
            self.cross_h.setVisible(False)
            return
        point = self.vb.mapSceneToView(pos)
        self.readout.setText(self.hover_text(point.x(), point.y()))
        self.cross_v.setPos(point.x())
        self.cross_h.setPos(point.y())
        self.cross_v.setVisible(True)
        self.cross_h.setVisible(True)

    def hover_text(self, x_log10: float, y_log10: float | None = None) -> str:
        """``f = 12.3 MHz, |Z| = 45.6 mΩ`` for the nearest grid point of each visible curve."""
        f = 10 ** x_log10
        parts = [f"f = {format_frequency(f, 3)}"]
        unit = z_label(self._unit)
        for s in self._series.values():
            if not s.visible or len(s.f_hz) == 0:
                continue
            i = int(np.argmin(np.abs(np.log10(s.f_hz) - x_log10)))
            val = max(s.abs_z[i], Z_PLOT_FLOOR_OHM) * self.scale
            label = "|Z|" if len(self._series) == 1 else s.name
            parts.append(f"{label} = {format_sig(val, 4)} {unit}")
        return ", ".join(parts)

    # -- export -----------------------------------------------------------------------------------
    def export_png(self, path: str, width: int = 1600) -> None:
        exporter = pg.exporters.ImageExporter(self.plot)
        exporter.parameters()["width"] = width
        exporter.export(path)


__all__ = ["ImpedancePlot", "LogFreqAxis", "engineering_frequency", "series_color", "PALETTE"]
