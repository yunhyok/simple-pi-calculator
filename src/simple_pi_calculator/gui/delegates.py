"""Item delegates: file browse, combo box, spin boxes and check boxes (DESIGN.md §5.5)."""

from __future__ import annotations

import os
import time
from typing import Callable, Sequence

from PySide6.QtCore import QAbstractItemModel, QEvent, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QWidget,
)

Index = QModelIndex | QPersistentModelIndex

DECAP_FILE_FILTER = ("Decap models (*.mod *.lib *.sp *.cir *.sub *.inc *.s2p);;"
                     "SPICE models (*.mod *.lib *.sp *.cir *.sub *.inc);;"
                     "Touchstone (*.s2p);;All files (*)")


class FileEditor(QWidget):
    """Line edit with a "…" browse button."""

    def __init__(self, parent: QWidget | None, start_dir: Callable[[], str],
                 file_filter: str):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.line = QLineEdit(self)
        self.button = QToolButton(self)
        self.button.setText("…")
        self.button.setToolTip("Browse for a decap model file")
        layout.addWidget(self.line, 1)
        layout.addWidget(self.button)
        self.setFocusProxy(self.line)
        self._start_dir = start_dir
        self._filter = file_filter
        self.button.clicked.connect(self.browse)

    def browse(self) -> None:
        current = self.line.text().strip()
        start = os.path.dirname(current) if current and os.path.isabs(current) else \
            self._start_dir()
        path, _ = QFileDialog.getOpenFileName(self, "Select decap model", start, self._filter)
        if path:
            self.line.setText(os.path.normpath(path))


class FileBrowseDelegate(QStyledItemDelegate):
    """Editor = line edit + browse button; the model stores the full path in EditRole."""

    def __init__(self, parent=None, start_dir: Callable[[], str] | None = None,
                 file_filter: str = DECAP_FILE_FILTER):
        super().__init__(parent)
        self._start_dir = start_dir or (lambda: os.getcwd())
        self._filter = file_filter

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem,
                     index: Index) -> QWidget:
        return FileEditor(parent, self._start_dir, self._filter)

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        if isinstance(editor, FileEditor):
            editor.line.setText(str(index.data(Qt.ItemDataRole.EditRole) or ""))

    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: Index) -> None:
        if isinstance(editor, FileEditor):
            model.setData(index, editor.line.text(), Qt.ItemDataRole.EditRole)


class ComboDelegate(QStyledItemDelegate):
    """Combo box with items supplied by a callable (e.g. current PWR names)."""

    def __init__(self, parent=None, items: Callable[[], Sequence[str]] | None = None,
                 editable: bool = False):
        super().__init__(parent)
        self._items = items or (lambda: [])
        self._editable = editable

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem,
                     index: Index) -> QWidget:
        combo = QComboBox(parent)
        combo.setEditable(self._editable)
        combo.addItems(list(self._items()))
        return combo

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        if isinstance(editor, QComboBox):
            text = str(index.data(Qt.ItemDataRole.EditRole) or "")
            i = editor.findText(text)
            if i >= 0:
                editor.setCurrentIndex(i)
            elif editor.isEditable():
                editor.setEditText(text)

    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: Index) -> None:
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class SpinDelegate(QStyledItemDelegate):
    """Integer spin box editor."""

    def __init__(self, parent=None, minimum: int = 0, maximum: int = 100000, step: int = 1):
        super().__init__(parent)
        self._min, self._max, self._step = minimum, maximum, step

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem,
                     index: Index) -> QWidget:
        spin = QSpinBox(parent)
        spin.setRange(self._min, self._max)
        spin.setSingleStep(self._step)
        return spin

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        if isinstance(editor, QSpinBox):
            try:
                editor.setValue(int(index.data(Qt.ItemDataRole.EditRole) or 0))
            except (TypeError, ValueError):
                editor.setValue(self._min)

    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: Index) -> None:
        if isinstance(editor, QSpinBox):
            editor.interpretText()
            model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class DoubleSpinDelegate(QStyledItemDelegate):
    """Floating-point spin box editor."""

    def __init__(self, parent=None, minimum: float = 0.0, maximum: float = 1e6,
                 decimals: int = 3, step: float = 0.1):
        super().__init__(parent)
        self._min, self._max, self._dec, self._step = minimum, maximum, decimals, step

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem,
                     index: Index) -> QWidget:
        spin = QDoubleSpinBox(parent)
        spin.setRange(self._min, self._max)
        spin.setDecimals(self._dec)
        spin.setSingleStep(self._step)
        return spin

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        if isinstance(editor, QDoubleSpinBox):
            try:
                editor.setValue(float(index.data(Qt.ItemDataRole.EditRole) or 0.0))
            except (TypeError, ValueError):
                editor.setValue(self._min)

    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: Index) -> None:
        if isinstance(editor, QDoubleSpinBox):
            editor.interpretText()
            model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class CheckBoxDelegate(QStyledItemDelegate):
    """Check-box cell that toggles on a click anywhere in the cell (DESIGN.md §5.5).

    Qt's default only toggles when the click hits the small indicator square at the left edge
    of the cell (a click elsewhere in the cell silently does nothing), and a double click — the
    edit gesture of every other cell — toggles a varying number of times depending on which
    of its events reach the delegate. Here one click anywhere in the cell toggles once, a
    double click toggles exactly once (its second click is ignored), and Space/Select toggles
    the current cell.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_toggle: tuple[float, int, int] | None = None  # (time, row, column)

    def _toggle(self, model: QAbstractItemModel, index: Index) -> bool:
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        value = state.value if hasattr(state, "value") else state
        checked = value is not None and int(value) == Qt.CheckState.Checked.value
        new = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
        self._last_toggle = (time.monotonic(), index.row(), index.column())
        return bool(model.setData(index, new.value, Qt.ItemDataRole.CheckStateRole))

    def _recently_toggled(self, index: Index) -> bool:
        if self._last_toggle is None:
            return False
        t, row, col = self._last_toggle
        interval = QApplication.doubleClickInterval() / 1000.0
        return (row, col) == (index.row(), index.column()) and time.monotonic() - t <= interval

    def editorEvent(self, event: QEvent, model: QAbstractItemModel,  # noqa: N802
                    option: QStyleOptionViewItem, index: Index) -> bool:
        flags = index.flags()
        if not (flags & Qt.ItemFlag.ItemIsUserCheckable) or not (flags & Qt.ItemFlag.ItemIsEnabled):
            return False
        etype = event.type()
        if etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                     QEvent.Type.MouseButtonDblClick):
            if not isinstance(event, QMouseEvent) \
                    or event.button() != Qt.MouseButton.LeftButton \
                    or not option.rect.contains(event.position().toPoint()):
                return False
            if etype == QEvent.Type.MouseButtonPress:
                return False  # the view selects the cell (and commits an open editor)
            if etype == QEvent.Type.MouseButtonDblClick:
                # the first click of the double click has already toggled (if it reached us)
                return True if self._recently_toggled(index) else self._toggle(model, index)
            if self._recently_toggled(index):
                return True  # release of the second click of a double click
            return self._toggle(model, index)
        if etype == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) \
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select):
            return self._toggle(model, index)
        return False


__all__ = ["FileBrowseDelegate", "CheckBoxDelegate", "FileEditor", "ComboDelegate", "SpinDelegate",
           "DoubleSpinDelegate", "DECAP_FILE_FILTER"]
