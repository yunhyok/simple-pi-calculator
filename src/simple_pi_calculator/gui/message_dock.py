"""Messages dock listing validation / import / compute issues (DESIGN.md §5.5)."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simple_pi_calculator.errors import Issue, Severity

ISSUE_ROLE = Qt.ItemDataRole.UserRole + 10


class MessageDock(QDockWidget):
    """``QTreeWidget`` (Severity icon, Code, Message, Source, Location).

    Issues are grouped by *category* (``"project"``, ``"import"``, ``"validation"``,
    ``"compute"``, ``"autosave"``); :meth:`set_issues` replaces one category.
    """

    issueActivated = Signal(object)  # Issue
    COLUMNS = ("Severity", "Code", "Message", "Source", "Location")

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Messages", parent)
        self.setObjectName("MessageDock")
        self._issues: dict[str, list[Issue]] = {}
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 2, 2, 2)
        top = QHBoxLayout()
        self.summary = QLabel("No messages", body)
        clear = QPushButton("Clear", body)
        clear.clicked.connect(self.clear)
        top.addWidget(self.summary, 1)
        top.addWidget(clear)
        layout.addLayout(top)
        self.tree = QTreeWidget(body)
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(list(self.COLUMNS))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 190)
        self.tree.setColumnWidth(2, 480)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree)
        self.setWidget(body)

    # -- API --------------------------------------------------------------------------------------
    def set_issues(self, category: str, issues: Iterable[Issue]) -> None:
        self._issues[category] = list(issues)
        self._rebuild()

    def add_issues(self, category: str, issues: Iterable[Issue]) -> None:
        self._issues.setdefault(category, []).extend(issues)
        self._rebuild()

    def clear_category(self, category: str) -> None:
        self._issues.pop(category, None)
        self._rebuild()

    def clear(self) -> None:
        self._issues.clear()
        self._rebuild()

    def issues(self, category: str | None = None) -> list[Issue]:
        """Issues of one category, or all categories without duplicates."""
        if category is not None:
            return list(self._issues.get(category, []))
        seen: set[Issue] = set()
        out: list[Issue] = []
        for group in self._issues.values():
            for issue in group:
                if issue not in seen:
                    seen.add(issue)
                    out.append(issue)
        return out

    def codes(self) -> list[str]:
        return [i.code for i in self.issues()]

    # -- internals --------------------------------------------------------------------------------
    def _icon(self, severity: Severity):
        style = self.style()
        pixmap = {
            Severity.ERROR: QStyle.StandardPixmap.SP_MessageBoxCritical,
            Severity.WARNING: QStyle.StandardPixmap.SP_MessageBoxWarning,
            Severity.INFO: QStyle.StandardPixmap.SP_MessageBoxInformation,
        }[severity]
        return style.standardIcon(pixmap)

    def _rebuild(self) -> None:
        self.tree.clear()
        all_issues = self.issues()
        ordered = sorted(enumerate(all_issues), key=lambda t: (-t[1].severity.value, t[0]))
        for _, issue in ordered:
            item = QTreeWidgetItem([issue.severity.name.title(), issue.code, issue.message,
                                    issue.source or "", issue.location or ""])
            item.setIcon(0, self._icon(issue.severity))
            item.setToolTip(2, issue.message)
            item.setData(0, ISSUE_ROLE, issue)
            self.tree.addTopLevelItem(item)
        n_err = sum(1 for i in all_issues if i.severity is Severity.ERROR)
        n_warn = sum(1 for i in all_issues if i.severity is Severity.WARNING)
        n_info = len(all_issues) - n_err - n_warn
        self.summary.setText("No messages" if not all_issues else
                             f"{n_err} error(s), {n_warn} warning(s), {n_info} info")

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        issue = item.data(0, ISSUE_ROLE)
        if isinstance(issue, Issue):
            self.issueActivated.emit(issue)


__all__ = ["MessageDock"]
