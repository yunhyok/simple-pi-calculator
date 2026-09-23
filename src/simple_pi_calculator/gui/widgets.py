"""Input widgets that do not change values by accident (DESIGN.md §5.5).

* :func:`install_wheel_guard` — a combo box or spin box ignores the mouse wheel unless the user
  clicked into it and it still has the keyboard focus. Otherwise the wheel event goes to the
  parent (a table or panel scrolls on). Qt's default changes the value of the widget under the
  mouse pointer, focused or not (Windows), so scrolling past the Decaps tab's ``PWR:`` filter
  or a σ / seed / sweep spin box silently changed it.
* :class:`CellSpinBox`, :class:`CellDoubleSpinBox`, :class:`CellComboBox` — table cell editors:
  wheel guarded, and Up / Down / Page Up / Page Down move to another row (committing the typed
  value) like in a spreadsheet instead of stepping the value or choosing the next item.
* :class:`ShortcutGuard` — while a table cell editor has the keyboard focus, keys without
  Ctrl / Alt / Meta (Esc, letters, digits, Space …) go to the editor, never to a window shortcut
  (e.g. Esc = Compute ▸ Cancel). Ctrl shortcuts (Ctrl+S, Ctrl+D …) and function keys (F5) keep
  working; the actions that use the inputs commit the open editor first.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QWidget,
)

#: keys that leave a cell editor towards another row instead of changing its value
NAVIGATION_KEYS = frozenset({Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp,
                             Qt.Key.Key_PageDown})


class WheelGuard(QObject):
    """Event filter behind :func:`install_wheel_guard`."""

    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self.widget = widget
        self.armed = False
        self._watched: set[int] = set()
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the wheel does not take the focus
        self._watch(widget)
        self._watch_line_edit()

    def _watch(self, obj: QObject) -> None:
        if id(obj) not in self._watched:
            self._watched.add(id(obj))
            obj.installEventFilter(self)

    def _watch_line_edit(self) -> None:
        edit = self.widget.lineEdit() if hasattr(self.widget, "lineEdit") else None
        if edit is not None:
            self._watch(edit)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        etype = event.type()
        if etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            self.armed = True  # a click into the widget or its line edit
        elif etype == QEvent.Type.FocusOut and obj is self.widget:
            self.armed = False
        elif etype == QEvent.Type.ChildAdded and obj is self.widget:
            self._watch_line_edit()  # setEditable(True) after the guard was installed
        elif etype == QEvent.Type.Wheel and obj is self.widget:
            if not (self.armed and self.widget.hasFocus()):
                # ignored + filtered: QApplication hands the wheel event on to the parent
                event.ignore()
                return True
        return False


def install_wheel_guard(*widgets: QWidget) -> None:
    """Wheel events change a combo box / spin box only after a click into it (see module doc)."""
    for widget in widgets:
        if getattr(widget, "_wheel_guard", None) is None:
            widget._wheel_guard = WheelGuard(widget)  # type: ignore[attr-defined]


def is_wheel_guarded(widget: QWidget) -> bool:
    return getattr(widget, "_wheel_guard", None) is not None


def _is_navigation(event: QKeyEvent) -> bool:
    mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
    return event.key() in NAVIGATION_KEYS and mods == Qt.KeyboardModifier.NoModifier


class CellSpinBox(QSpinBox):
    """Integer cell editor (see module doc)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        install_wheel_guard(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if _is_navigation(event):
            event.ignore()  # the view moves the current row and commits the value
            return
        super().keyPressEvent(event)


class CellDoubleSpinBox(QDoubleSpinBox):
    """Floating-point cell editor (see module doc)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        install_wheel_guard(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if _is_navigation(event):
            event.ignore()
            return
        super().keyPressEvent(event)


class CellComboBox(QComboBox):
    """Combo box cell editor (see module doc); Alt+Down / F4 still open the list."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        install_wheel_guard(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if _is_navigation(event):
            event.ignore()
            return
        super().keyPressEvent(event)


def cell_editor_view(widget: QWidget | None) -> QAbstractItemView | None:
    """The item view whose open cell editor contains ``widget`` (``None`` otherwise)."""
    w = widget
    while w is not None:
        parent = w.parentWidget()
        if parent is not None:
            view = parent.parentWidget()
            if isinstance(view, QAbstractItemView) and parent is view.viewport():
                return view if view.state() == QAbstractItemView.State.EditingState else None
        w = parent
    return None


class ShortcutGuard(QObject):
    """Application event filter: plain keys belong to an open table cell editor (module doc).

    Only shortcut overrides whose focus widget lies inside ``window`` are affected.
    """

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window = window
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
            focus = QApplication.focusWidget()
            if (focus is not None and obj is focus and self.window.isAncestorOf(focus)
                    and cell_editor_view(focus) is not None and self._plain(event)):
                event.accept()  # the editor receives the key press; no shortcut fires
        return False

    @staticmethod
    def _plain(event: QKeyEvent) -> bool:
        mods = event.modifiers() & ~(Qt.KeyboardModifier.KeypadModifier
                                     | Qt.KeyboardModifier.ShiftModifier)
        if mods != Qt.KeyboardModifier.NoModifier:
            return False
        key = event.key()
        code = int(key.value if hasattr(key, "value") else key)
        return not (Qt.Key.Key_F1.value <= code <= Qt.Key.Key_F35.value)


__all__ = ["install_wheel_guard", "is_wheel_guarded", "WheelGuard", "CellSpinBox",
           "CellDoubleSpinBox", "CellComboBox", "ShortcutGuard", "cell_editor_view",
           "NAVIGATION_KEYS"]
