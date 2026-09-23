"""Column widths of the tables (DESIGN.md §5.5).

Every column is ``QHeaderView.Interactive`` so the user can drag its width. The automatic layout
(fit to contents, the file-name column filling the free space, the readout columns sharing the
width) only sizes columns the user has **not** resized, and it is computed on construction, on a
model reset (project load, import) and when the viewport is resized — never on an edit
(``dataChanged``), so a width the user chose is never overwritten. The user widths can be saved in
and restored from the session (``session.window.column_widths``).

``ResizeToContents`` / ``Stretch`` sections, used before 0.4.1, cannot be resized by the user and
are re-computed on every data change; they are not used any more.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QWidget

#: Upper bound of an automatic (fit-to-contents) column width in px; the user may go wider.
MAX_AUTO_WIDTH = 320


class ColumnSizer(QObject):
    """Tracks the columns the user resized and keeps their widths.

    Widths set by this class are *programmatic*; any other change of a section size is a user
    resize (drag of the header divider or a double click on it).  Subclasses implement
    :meth:`auto_layout` for the remaining (automatic) columns.
    """

    #: a column width was changed by the user (for the auto-save)
    userResized = Signal()

    def __init__(self, header: QHeaderView, parent: QObject | None = None):
        super().__init__(parent if parent is not None else header)
        self.header = header
        self._programmatic = 0
        self._user: dict[int, int] = {}   # column -> width chosen by the user
        self._pending = False
        self._programmatic += 1
        try:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.setStretchLastSection(False)
            header.setMinimumSectionSize(28)
        finally:
            self._programmatic -= 1
        header.sectionResized.connect(self._on_section_resized)

    # -- helpers ----------------------------------------------------------------------------------
    def column_count(self) -> int:
        return int(self.header.count())

    def set_width(self, column: int, width: int) -> None:
        """Programmatic resize (not recorded as a user width)."""
        width = max(int(width), self.header.minimumSectionSize())
        if self.header.sectionSize(column) == width:
            return
        self._programmatic += 1
        try:
            self.header.resizeSection(column, width)
        finally:
            self._programmatic -= 1

    def is_user_sized(self, column: int) -> bool:
        return column in self._user

    def user_columns(self) -> list[int]:
        return sorted(self._user)

    def _on_section_resized(self, index: int, _old: int, new: int) -> None:
        if self._programmatic:
            return
        header = self.header
        if header.stretchLastSection() and index == header.logicalIndex(header.count() - 1):
            return  # Qt keeps the stretched last section filling the width
        self._user[index] = int(new)
        self.on_user_resized(index)
        self.userResized.emit()

    def on_user_resized(self, index: int) -> None:  # pragma: no cover - hook
        """Called after the user resized ``index`` (subclasses re-run the automatic layout)."""

    def schedule(self) -> None:
        """Run :meth:`apply` once from the event loop (coalesces resize bursts)."""
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._run_scheduled)

    def _run_scheduled(self) -> None:
        self._pending = False
        try:
            self.apply()
        except RuntimeError:  # the widgets were deleted meanwhile
            pass

    # -- layout -----------------------------------------------------------------------------------
    def apply(self) -> None:
        """Re-apply the user widths (a model reset may have re-created the sections), then lay
        out the automatic columns."""
        n = self.column_count()
        for col, width in list(self._user.items()):
            if col < n:
                self.set_width(col, width)
        self.auto_layout()

    def auto_layout(self) -> None:  # pragma: no cover - hook
        """Size the columns the user has not resized."""

    # -- session ----------------------------------------------------------------------------------
    def widths(self) -> list[int]:
        """Per column: the user width in px, or 0 for an automatic column (session state)."""
        return [int(self._user.get(c, 0)) for c in range(self.column_count())]

    def restore(self, widths: Sequence[int] | None) -> bool:
        """Restore :meth:`widths`; ignored (automatic layout kept) when the number of columns
        differs from the table's, e.g. after a version added a column."""
        if not widths or len(widths) != self.column_count():
            return False
        self._user = {c: int(w) for c, w in enumerate(widths)
                      if isinstance(w, (int, float)) and not isinstance(w, bool) and int(w) > 0}
        self.apply()
        return True

    def reset(self) -> None:
        """Forget the user widths and return to the automatic layout."""
        self._user.clear()
        self.apply()


class FillColumns(ColumnSizer):
    """Input table layout: automatic columns fit their contents (header included, capped at
    :data:`MAX_AUTO_WIDTH`); the ``fill`` column takes the viewport width left by the ``primary``
    columns (at least ``minimum``) until the user resizes it.

    Unlike ``QHeaderView.Stretch`` the fill column stays resizable, and the primary columns keep
    fitting the visible width when further (derived) columns follow and the table scrolls
    horizontally.
    """

    def __init__(self, view: QAbstractItemView, fill: int, primary: Sequence[int] | None = None,
                 minimum: int = 120):
        super().__init__(view.horizontalHeader(), view)
        self.view = view
        self.fill = fill
        self._primary = None if primary is None else list(primary)
        self.minimum = minimum
        view.viewport().installEventFilter(self)
        model = view.model()
        if model is not None:
            model.modelReset.connect(self.apply)
        self.apply()

    @property
    def primary(self) -> list[int]:
        return list(range(self.column_count())) if self._primary is None else self._primary

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            self.schedule()
        return False

    def on_user_resized(self, index: int) -> None:
        if index != self.fill and index in self.primary:
            self.schedule()

    def content_width(self, column: int) -> int:
        header = self.header
        return min(MAX_AUTO_WIDTH, max(header.sectionSizeHint(column),
                                       self.view.sizeHintForColumn(column)))

    def auto_layout(self) -> None:
        n = self.column_count()
        for col in range(n):
            if col != self.fill and not self.is_user_sized(col):
                self.set_width(col, self.content_width(col))
        self.fill_now()

    def fill_now(self) -> None:
        """Give the fill column the free viewport width (unless the user sized it)."""
        if self.is_user_sized(self.fill) or self.fill >= self.column_count():
            return
        header = self.header
        used = sum(header.sectionSize(c) for c in self.primary if c != self.fill)
        self.set_width(self.fill, max(self.minimum, self.view.viewport().width() - used - 1))


class ShareColumns(ColumnSizer):
    """Readout table layout: automatic columns share the table width equally when there is room,
    otherwise keep their content width and the table scrolls horizontally (it never enforces a
    minimum pane width)."""

    def __init__(self, view: QAbstractItemView):
        super().__init__(view.horizontalHeader(), view)
        self.view = view
        view.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.view and event.type() == QEvent.Type.Resize:
            self.apply()
        return False

    def auto_layout(self) -> None:
        view = self.view
        n = self.column_count()
        if n <= 0:
            return
        header = self.header
        # Width available to the columns, computed from the table frame (not the viewport) and
        # always reserving room for a vertical scroll bar: it must not depend on scroll-bar
        # visibility, or showing/hiding a bar would re-trigger this in a loop.
        style = view.style()
        bar = style.pixelMetric(style.PixelMetric.PM_ScrollBarExtent, None, view)
        vheader = view.verticalHeader()
        vwidth = vheader.width() if vheader.isVisible() else 0
        free = max(0, view.contentsRect().width() - vwidth - bar)
        free -= sum(header.sectionSize(c) for c in range(n) if self.is_user_sized(c))
        auto = [c for c in range(n) if not self.is_user_sized(c)]
        if not auto:
            return
        share = max(0, free) // len(auto)
        for col in auto:
            wanted = max(header.sectionSizeHint(col), view.sizeHintForColumn(col))
            self.set_width(col, max(wanted, share))


def install_column_sizer(widget: QWidget) -> ColumnSizer:
    """Plain :class:`ColumnSizer` for a header without automatic layout (e.g. the Messages
    tree): interactive columns whose user widths are tracked for the session."""
    header = widget.header() if hasattr(widget, "header") else widget.horizontalHeader()
    stretch = header.stretchLastSection()
    sizer = ColumnSizer(header, widget)
    header.setStretchLastSection(stretch)
    return sizer


__all__ = ["ColumnSizer", "FillColumns", "ShareColumns", "install_column_sizer",
           "MAX_AUTO_WIDTH"]
