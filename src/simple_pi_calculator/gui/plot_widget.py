"""Impedance plot based on pyqtgraph (DESIGN.md §5.6).

``ImpedancePlot`` shows one or more PWR curves in log-log coordinates. In log mode pyqtgraph
takes data in linear units but the ViewBox coordinates are log10 — marker lines, text positions
and ranges therefore use log10 values (§5.6 pitfall).
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import sys
from typing import Any, Iterator, Sequence

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters  # noqa: F401 - explicit import so PyInstaller collects it (§5.6)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication

from simple_pi_calculator.constants import MARKER_FREQUENCIES_HZ, Z_PLOT_FLOOR_OHM
from simple_pi_calculator.core.units import format_frequency, format_sig, z_label, z_scale
from simple_pi_calculator.io.project_io import PlotView

pg.setConfigOptions(antialias=True, background="w", foreground="k")

PALETTE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf",
           "#bcbd22", "#7f7f7f", "#d62728")
MARKER_COLOR = "#d62728"
RESET_VIEW_TEXT = "Reset view"
RESET_VIEW_SHORTCUT = "Ctrl+D"
IMAGE_FORMATS = ("png", "svg")


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
        self._show_legend = True
        self._series: dict[str, _Series] = {}
        self._results: list[Any] = []
        self._colors: list[str] = []
        self._programmatic = 0
        # "fresh" view = fitted to the visible curves and not zoomed/panned by the user since
        # (§5.6): visibility and unit changes re-fit only while it is fresh.
        self._view_fresh = True
        self._fitted_range: tuple[tuple[float, float], tuple[float, float]] | None = None

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
        # the padding is size-dependent, so a resize re-fits the (still fresh) default view
        self.vb.sigResized.connect(lambda *_: self._refit_if_fresh())
        self._mouse_proxy = pg.SignalProxy(self.scene().sigMouseMoved, rateLimit=30,
                                           slot=self._on_move)
        self._last_auto = tuple(self.vb.autoRangeEnabled())
        self.vb.setRange(xRange=(5, 9), yRange=(-1, 3), padding=0)
        self._fitted_range = ((5.0, 9.0), (-1.0, 3.0))
        self._install_reset_view_menu()

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
        self._results = []
        self._colors = []
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
            self._results = list(results)
            self._colors = [colors[i] if colors is not None and i < len(colors)
                            else series_color(i) for i in range(len(results))]
            self._rebuild_legend()
            self._update_markers()
            self._apply_default_view()
        finally:
            self._programmatic -= 1

    def _legend_remove(self, item: pg.PlotDataItem) -> None:
        try:
            self.legend.removeItem(item)
        except Exception:  # noqa: BLE001
            pass

    def _rebuild_legend(self) -> None:
        """Legend entries in series order for the **visible** curves only (a hidden curve would
        otherwise keep an empty row in the legend)."""
        self.legend.clear()
        for s in self._series.values():
            if s.curve is None or not s.visible:
                continue
            self.legend.addItem(s.curve, s.name)
            if s.plane_curve is not None and self._show_plane_only:
                self.legend.addItem(s.plane_curve, f"{s.name} plane only")
        self.legend.setVisible(bool(self._show_legend))

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
        """Rescale without recompute; in the fresh (default) view the range is re-fitted, after a
        manual zoom the X range is kept and the Y range shifted by log10(s_new/s_old)."""
        if unit == self._unit:
            return
        old = self.scale
        self._unit = unit
        new = self.scale
        self._programmatic += 1
        try:
            fresh = self.is_default_view()
            (x0, x1), (y0, y1) = self.vb.viewRange()
            for s in self._series.values():
                if s.curve is not None:
                    s.curve.setData(s.f_hz, self._scaled(s.abs_z))
                if s.plane_curve is not None and s.abs_plane is not None:
                    s.plane_curve.setData(s.f_hz, self._scaled(s.abs_plane))
            self._update_markers()
            self._update_axis_label()
            if fresh:
                self._apply_default_view()
            else:
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
            s.plane_curve.setVisible(bool(self._show_plane_only and s.visible))
        self._rebuild_legend()
        self._refit_if_fresh()

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

    def set_legend_visible(self, visible: bool) -> None:
        """Show or hide the in-plot legend (View ▸ Show Plot Legend, §5.5); with the curve list
        beside the *All PWRs* plot it is redundant on screen, so it is off by default."""
        self._show_legend = bool(visible)
        self.legend.setVisible(self._show_legend)

    def legend_visible(self) -> bool:
        return self._show_legend

    def set_curve_visible(self, name: str, visible: bool) -> None:
        s = self._series.get(name)
        if s is None:
            return
        s.visible = bool(visible)
        if s.curve is not None:
            s.curve.setVisible(bool(s.visible))
        if s.plane_curve is not None:
            s.plane_curve.setVisible(bool(s.visible and self._show_plane_only))
        self._rebuild_legend()
        self._update_markers()
        self._refit_if_fresh()

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
    def _install_reset_view_menu(self) -> None:
        """"Reset view (Ctrl+D)" at the top of the ViewBox context menu; pyqtgraph's own
        "View All" entry is re-routed to the same default view (§5.6)."""
        menu = getattr(self.vb, "menu", None)
        if menu is None:
            return
        self.reset_view_action = QAction(RESET_VIEW_TEXT, menu)
        self.reset_view_action.setShortcut(QKeySequence(RESET_VIEW_SHORTCUT))
        # the application-wide shortcut lives in the main window; here it is only a hint
        self.reset_view_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.reset_view_action.triggered.connect(lambda _checked=False: self.reset_view())
        actions = menu.actions()
        if actions:
            menu.insertAction(actions[0], self.reset_view_action)
        else:
            menu.addAction(self.reset_view_action)
        view_all = getattr(menu, "viewAll", None)
        if view_all is not None:
            with contextlib.suppress(RuntimeError, TypeError):
                view_all.triggered.disconnect()
            view_all.triggered.connect(lambda _checked=False: self.reset_view())

    def data_bounds(self, only_visible: bool = True
                    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """log10 bounds ``((x0, x1), (y0, y1))`` of the curve data — the sweep in x and |Z| in the
        current unit in y — over the visible curves (plus the visible plane-only curves) or over
        all curves. ``None`` when there is no data. Markers are vertical lines / points on the
        curves and never widen the bounds."""
        x_lo, x_hi, y_lo, y_hi = math.inf, -math.inf, math.inf, -math.inf
        for s in self._series.values():
            if only_visible and not s.visible:
                continue
            f = np.asarray(s.f_hz, dtype=float)
            f = f[np.isfinite(f) & (f > 0.0)]
            if f.size == 0:
                continue
            x_lo = min(x_lo, float(np.log10(f.min())))
            x_hi = max(x_hi, float(np.log10(f.max())))
            arrays = [self._scaled(s.abs_z)]
            if s.abs_plane is not None and self._show_plane_only:
                arrays.append(self._scaled(s.abs_plane))
            for values in arrays:
                v = np.asarray(values, dtype=float)
                v = v[np.isfinite(v) & (v > 0.0)]
                if v.size == 0:
                    continue
                y_lo = min(y_lo, float(np.log10(v.min())))
                y_hi = max(y_hi, float(np.log10(v.max())))
        if not (math.isfinite(x_lo) and math.isfinite(x_hi)
                and math.isfinite(y_lo) and math.isfinite(y_hi)):
            return None
        return (x_lo, x_hi), (y_lo, y_hi)

    def fit_range(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """The default view range: :meth:`data_bounds` of the visible curves (all curves when
        nothing is visible) plus the standard padding (2 … 5 % of the span, size-dependent)."""
        bounds = self.data_bounds(only_visible=True)
        if bounds is None:
            bounds = self.data_bounds(only_visible=False)
        if bounds is None:
            return None
        (x0, x1), (y0, y1) = bounds
        if x1 - x0 < 1e-12:
            x0, x1 = x0 - 0.5, x1 + 0.5
        if y1 - y0 < 1e-12:
            y0, y1 = y0 - 0.5, y1 + 0.5
        px = min(max(float(self.vb.suggestPadding(0)), 0.02), 0.05)
        py = min(max(float(self.vb.suggestPadding(1)), 0.02), 0.05)
        wx, wy = x1 - x0, y1 - y0
        return (x0 - wx * px, x1 + wx * px), (y0 - wy * py, y1 + wy * py)

    def _apply_default_view(self) -> None:
        """The default view of a fresh compute: log–log axes and the range explicitly fitted to
        the **visible** curves (:meth:`fit_range`) with auto-range switched off, so hidden curves
        can never widen the view and pyqtgraph cannot re-fit behind our back (§5.6)."""
        self.plot.setLogMode(x=True, y=True)
        self.vb.disableAutoRange()
        rng = self.fit_range()
        if rng is not None:
            (x0, x1), (y0, y1) = rng
            self.vb.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=0)
            self._fitted_range = ((x0, x1), (y0, y1))
        self._view_fresh = True

    def _refit_if_fresh(self) -> None:
        """Re-fit after a visibility / plane-only / unit change, but only while the view is still
        the fitted one — a manual zoom or pan is never overruled."""
        if not self.is_default_view():
            return
        self._programmatic += 1
        try:
            self._apply_default_view()
        finally:
            self._programmatic -= 1

    def reset_view(self) -> None:
        """Restore the default view (toolbar "Reset view", Ctrl+D, context menu, "View All")."""
        self._programmatic += 1
        try:
            self._apply_default_view()
            self.set_markers_visible(self._show_markers)
        finally:
            self._programmatic -= 1
        self.viewChanged.emit()

    def reset_zoom(self) -> None:
        """Backwards-compatible alias of :meth:`reset_view`."""
        self.reset_view()

    def view_is_fresh(self) -> bool:
        """``True`` while the user has not zoomed or panned since the last fit (§5.6)."""
        return bool(self._view_fresh)

    def is_default_view(self, tol: float = 1e-9) -> bool:
        """``True`` when the view is still the fitted default view: not manually zoomed or panned
        and the range equal to the one :meth:`_apply_default_view` last set."""
        if not self._view_fresh or self._fitted_range is None:
            return False
        (x0, x1), (y0, y1) = self.vb.viewRange()
        (fx0, fx1), (fy0, fy1) = self._fitted_range
        tx = tol + 1e-6 * abs(fx1 - fx0)
        ty = tol + 1e-6 * abs(fy1 - fy0)
        return (abs(x0 - fx0) <= tx and abs(x1 - fx1) <= tx
                and abs(y0 - fy0) <= ty and abs(y1 - fy1) <= ty)

    def view_state(self) -> PlotView:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        return PlotView(auto_range=self.is_default_view(),
                        x_range_log10=(float(x0), float(x1)),
                        y_range_log10=(float(y0), float(y1)))

    def apply_view_state(self, view: PlotView) -> None:
        self._programmatic += 1
        try:
            if view.auto_range or view.x_range_log10 is None or view.y_range_log10 is None:
                self._apply_default_view()
            else:
                self.vb.disableAutoRange()
                self.vb.setRange(xRange=view.x_range_log10, yRange=view.y_range_log10,
                                 padding=0)
                self._view_fresh = False
        finally:
            self._programmatic -= 1

    def _on_manual_range(self, *_args) -> None:
        """Any mouse zoom / pan (``sigRangeChangedManually``) makes the view "not fresh", so it is
        no longer re-fitted when curves are hidden or the unit changes."""
        self._view_fresh = False
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

    def results(self) -> list[Any]:
        return list(self._results)

    def make_export_copy(self, width: int, height: int, keep_zoom: bool = False
                         ) -> "ImpedancePlot":
        """An off-screen twin of this plot at ``width`` × ``height`` px with the same data, unit,
        marker / plane-only / curve-visibility settings and title; default view unless
        ``keep_zoom`` (then the current log10 ranges). The hover readout row is removed.

        The twin is shown with ``WA_DontShowOnScreen`` and rendered once so that the axis text
        widths and the legend are laid out before the real export (a widget that was never shown,
        e.g. a background tab, otherwise exports with collapsed axes).
        """
        twin = ImpedancePlot(title=self._title, unit=self._unit,
                             marker_frequencies=self._marker_freqs,
                             marker_texts=self._marker_texts_enabled)
        twin._show_markers = self._show_markers
        twin._show_plane_only = self._show_plane_only
        # an exported image has no curve list beside it, so it always carries the legend
        twin._show_legend = True
        twin._stale = self._stale
        twin._update_title()
        twin.ci.removeItem(twin.readout)
        twin.set_results(self._results, self._colors)
        twin.set_plane_only_visible(self._show_plane_only)
        twin.set_markers_visible(self._show_markers)
        for name, s in self._series.items():
            twin.set_curve_visible(name, s.visible)
        twin.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        twin.resize(int(width), int(height))
        twin.show()
        app = QApplication.instance()
        for _ in range(2):
            if app is not None:
                app.processEvents()
            twin.grab()  # forces a paint: axes compute their text size and relayout
        if keep_zoom:
            (x0, x1), (y0, y1) = self.vb.viewRange()
            twin.vb.disableAutoRange()
            twin.vb.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=0)
        else:
            twin._apply_default_view()
        if app is not None:
            app.processEvents()
        twin.grab()
        return twin

    def export_image(self, path: str, width: int = 1600, height: int = 1000,
                     keep_zoom: bool = False) -> str:
        """Save the plot as PNG (``ImageExporter``) or SVG (``SVGExporter``) — chosen by the
        extension of ``path`` — at exactly ``width`` × ``height`` px, rendered from an off-screen
        twin (:meth:`make_export_copy`) so the on-screen view is not touched."""
        width, height = int(width), int(height)
        if width < 50 or height < 50:
            raise ValueError(f"image size must be at least 50 x 50 px, got {width} x {height}")
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext not in IMAGE_FORMATS:
            raise ValueError(f"unsupported image format {ext!r} (use PNG or SVG)")
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        twin = self.make_export_copy(width, height, keep_zoom)
        try:
            scene = twin.scene()
            if ext == "png":
                exporter = pg.exporters.ImageExporter(scene)
                params = exporter.parameters()
                params.param("width").setValue(width, blockSignal=exporter.widthChanged)
                params.param("height").setValue(height, blockSignal=exporter.heightChanged)
                exporter.export(path)
            else:
                exporter = pg.exporters.SVGExporter(scene)
                with _svg_close_path_workaround():
                    exporter.export(path)
        finally:
            twin.hide()
            twin.deleteLater()
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise OSError(f"the image exporter did not write {path}")
        return path


@contextlib.contextmanager
def _svg_close_path_workaround() -> Iterator[None]:
    """pyqtgraph ≤ 0.14 ``SVGExporter.correctCoordinates`` fails on the ``Z`` (close-path) token
    that Qt 6 writes for the ViewBox background rectangle (``ValueError: not enough values to
    unpack``). Temporarily strip standalone ``Z`` tokens before the coordinate correction and put
    them back afterwards."""
    module = sys.modules.get("pyqtgraph.exporters.SVGExporter")
    original = getattr(module, "correctCoordinates", None)
    if module is None or original is None:
        yield
        return

    def patched(node, defs, item, options):
        closed = []
        for element in node.getElementsByTagName("path"):
            d = element.getAttribute("d")
            if re.search(r"(^|\s)[Zz](\s|$)", d):
                element.setAttribute("d", re.sub(r"(^|\s)[Zz](?=\s|$)", " ", d).strip())
                closed.append(element)
        result = original(node, defs, item, options)
        for element in closed:
            d = element.getAttribute("d").strip()
            if d:
                element.setAttribute("d", d + " Z")
        return result

    module.correctCoordinates = patched
    try:
        yield
    finally:
        module.correctCoordinates = original


__all__ = ["ImpedancePlot", "LogFreqAxis", "engineering_frequency", "series_color", "PALETTE",
           "RESET_VIEW_TEXT", "RESET_VIEW_SHORTCUT", "IMAGE_FORMATS"]
