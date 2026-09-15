"""Item delegates: file browse, combo box and spin boxes (DESIGN.md §5.5)."""

from __future__ import annotations

import os
from typing import Callable, Sequence

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
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


__all__ = ["FileBrowseDelegate", "FileEditor", "ComboDelegate", "SpinDelegate",
           "DoubleSpinDelegate", "DECAP_FILE_FILTER"]
