"""Help viewer (DESIGN.md §6.1, §6.2)."""

from __future__ import annotations

import importlib.resources
import os

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QTextDocument
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QTextBrowser,
    QToolBar,
    QWidget,
)

#: (file, contents title) in the order of §6.2, plus the troubleshooting page
HELP_PAGES: tuple[tuple[str, str], ...] = (
    ("index.html", "Overview"),
    ("getting_started.html", "Getting Started"),
    ("input_stackup.html", "Input: Stack-up"),
    ("input_vias.html", "Input: Via settings"),
    ("input_pwr.html", "Input: PWR list"),
    ("input_decaps.html", "Input: Decap list"),
    ("spice_models.html", "SPICE .mod models"),
    ("touchstone.html", "Touchstone .s2p models"),
    ("physics.html", "Physics and models"),
    ("results.html", "Results and plot"),
    ("project_file.html", "Projects and auto-save"),
    ("limitations.html", "Limitations"),
    ("references.html", "References"),
    ("troubleshooting.html", "Troubleshooting"),
    ("about.html", "About and licenses"),
)

PAGE_ROLE = Qt.ItemDataRole.UserRole + 1


def help_dir() -> str:
    """Real filesystem path of the bundled help folder (§6.1)."""
    try:
        path = importlib.resources.files("simple_pi_calculator") / "help"
        text = os.fspath(path)  # type: ignore[arg-type]
        if os.path.isdir(text):
            return text
    except (TypeError, ModuleNotFoundError):  # pragma: no cover - zipped package
        pass
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "help")


class HelpWindow(QMainWindow):
    """Contents list + ``QTextBrowser`` with Back/Forward/Home, Open in Browser and find."""

    def __init__(self, parent: QWidget | None = None, directory: str | None = None):
        super().__init__(parent)
        self.setObjectName("HelpWindow")
        self.setWindowTitle("Simple PI Calculator Help")
        self.resize(1000, 720)
        self.directory = directory or help_dir()

        self.contents = QListWidget(self)
        for file_name, title in HELP_PAGES:
            if os.path.isfile(os.path.join(self.directory, file_name)):
                item = QListWidgetItem(title)
                item.setData(PAGE_ROLE, file_name)
                self.contents.addItem(item)
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setOpenLinks(True)
        self.browser.setSearchPaths([self.directory])

        splitter = QSplitter(self)
        splitter.addWidget(self.contents)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 780])
        self.setCentralWidget(splitter)

        bar = QToolBar("Help navigation", self)
        bar.setObjectName("HelpToolbar")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.back_action = QAction("Back", self)
        self.back_action.setShortcut(QKeySequence.StandardKey.Back)
        self.back_action.triggered.connect(self.browser.backward)
        self.forward_action = QAction("Forward", self)
        self.forward_action.setShortcut(QKeySequence.StandardKey.Forward)
        self.forward_action.triggered.connect(self.browser.forward)
        self.home_action = QAction("Home", self)
        self.home_action.triggered.connect(lambda: self.show_page("index.html"))
        self.browser_action = QAction("Open in Browser", self)
        self.browser_action.triggered.connect(self.open_in_browser)
        for act in (self.back_action, self.forward_action, self.home_action):
            bar.addAction(act)
        bar.addSeparator()
        bar.addAction(self.browser_action)
        bar.addSeparator()
        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Find in page…")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.setMaximumWidth(240)
        self.find_edit.returnPressed.connect(self.find_next)
        bar.addWidget(self.find_edit)

        self.back_action.setEnabled(False)
        self.forward_action.setEnabled(False)
        self.browser.backwardAvailable.connect(self.back_action.setEnabled)
        self.browser.forwardAvailable.connect(self.forward_action.setEnabled)
        self.browser.sourceChanged.connect(self._sync_contents)
        self.contents.currentItemChanged.connect(self._on_contents)
        self.show_page("index.html")

    # -- navigation -------------------------------------------------------------------------------
    def page_files(self) -> list[str]:
        return [self.contents.item(i).data(PAGE_ROLE) for i in range(self.contents.count())]

    def show_page(self, file_name: str, anchor: str = "") -> None:
        path = os.path.join(self.directory, file_name)
        url = QUrl.fromLocalFile(path)
        if anchor:
            url.setFragment(anchor)
        self.browser.setSource(url)

    def current_page_path(self) -> str:
        return self.browser.source().toLocalFile()

    def current_page_file(self) -> str:
        return os.path.basename(self.current_page_path())

    def open_in_browser(self) -> bool:
        path = self.current_page_path() or os.path.join(self.directory, "index.html")
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def find_next(self) -> bool:
        text = self.find_edit.text()
        if not text:
            return False
        if self.browser.find(text):
            return True
        cursor = self.browser.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.browser.setTextCursor(cursor)
        return self.browser.find(text)

    def missing_images(self) -> list[str]:
        """``<img>`` sources of the current page that cannot be loaded (§8.12)."""
        doc = self.browser.document()
        missing: list[str] = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        name = fmt.toImageFormat().name()
                        res = self.browser.loadResource(
                            QTextDocument.ResourceType.ImageResource.value, QUrl(name))
                        if res is None or (hasattr(res, "isNull") and res.isNull()):
                            missing.append(name)
                it += 1
            block = block.next()
        return missing

    def _sync_contents(self, url: QUrl) -> None:
        name = os.path.basename(url.toLocalFile() or url.path())
        for i in range(self.contents.count()):
            item = self.contents.item(i)
            if item.data(PAGE_ROLE) == name:
                if self.contents.currentRow() != i:
                    self.contents.blockSignals(True)
                    self.contents.setCurrentRow(i)
                    self.contents.blockSignals(False)
                break
        self.setWindowTitle(f"Simple PI Calculator Help — {self.browser.documentTitle()}")

    def _on_contents(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            file_name = current.data(PAGE_ROLE)
            if file_name != self.current_page_file():
                self.show_page(file_name)


__all__ = ["HelpWindow", "HELP_PAGES", "help_dir"]
