"""QApplication bootstrap and command line entry point (DESIGN.md §5.1, §5.7, §5.8.1).

``simple-pi-calculator [--no-restore] [--self-test] [project.spical.json]``

``--self-test`` imports every module, creates the main window without touching the auto-save
directory (works with ``QT_QPA_PLATFORM=offscreen``) and exits with 0 on success — used by CI and
the packaged build.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import logging.handlers
import os
import pkgutil
import sys
import traceback
from typing import Sequence

from simple_pi_calculator import __version__
from simple_pi_calculator.constants import APP_DISPLAY_NAME, APP_NAME

log = logging.getLogger("simple_pi_calculator")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="simple-pi-calculator", description=APP_DISPLAY_NAME)
    parser.add_argument("project", nargs="?", help="project file (*.spical.json) to open")
    parser.add_argument("--no-restore", action="store_true",
                        help="start with defaults instead of restoring the auto-save")
    parser.add_argument("--self-test", action="store_true",
                        help="import all modules, create the main window and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(list(argv) if argv is not None else None)


def configure_application_identity(app) -> None:
    """§5.8.1: must run before any window or QStandardPaths lookup."""
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("")
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setApplicationVersion(__version__)


def setup_logging(directory: str) -> str | None:
    """Rotating log file ``<appdata>/logs/app.log`` (1 MB × 3, §5.7)."""
    try:
        folder = os.path.join(directory, "logs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "app.log")
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                                       encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        return path
    except OSError:
        return None


def install_excepthook(log_path: str | None) -> None:
    """Uncaught exceptions → log + message box with traceback (§5.7); never exits the app."""

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Uncaught exception:\n%s", text)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                box = QMessageBox(QMessageBox.Icon.Critical, APP_DISPLAY_NAME,
                                  "An unexpected error occurred. The application state has been "
                                  "auto-saved." + (f"\n\nLog file: {log_path}" if log_path
                                                   else ""))
                box.setDetailedText(text)
                box.exec()
                return
        except Exception:  # noqa: BLE001
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def import_all_modules() -> list[str]:
    """Import every module of the package (self-test). Returns the imported module names.

    Engine modules that are not present in this build are skipped; any other import error
    propagates.
    """
    import simple_pi_calculator as pkg
    names: list[str] = []
    for info in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        if info.name.endswith("__main__"):
            continue
        importlib.import_module(info.name)
        names.append(info.name)
    return names


def run_self_test(app) -> int:
    """Create the main window offscreen-safe without auto-save / settings (§8.12)."""
    from PySide6.QtCore import QTimer

    from simple_pi_calculator.gui.help_window import HelpWindow
    from simple_pi_calculator.gui.main_window import MainWindow
    try:
        modules = import_all_modules()
        window = MainWindow(restore=False, autosave=False, use_settings=False,
                            auto_compute=False)
        window.show()
        help_win = HelpWindow()
        help_win.show_page("index.html")
        QTimer.singleShot(0, app.quit)
        app.exec()
        help_win.close()
        window.close()
        print(f"self-test OK: {APP_DISPLAY_NAME} {__version__}, {len(modules)} modules imported")
        return 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    from PySide6.QtWidgets import QApplication
    import pyqtgraph as pg

    app = QApplication.instance() or QApplication([sys.argv[0]])
    configure_application_identity(app)
    pg.setConfigOptions(antialias=True, background="w", foreground="k")
    try:
        from PySide6.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources",
                                 "app.png")
        if os.path.isfile(icon_path):
            app.setWindowIcon(QIcon(icon_path))
    except Exception:  # noqa: BLE001
        pass

    if args.self_test:
        return run_self_test(app)

    from simple_pi_calculator.gui.main_window import MainWindow
    from simple_pi_calculator.gui.persistence import appdata_dir, ensure_dir
    directory = ensure_dir(appdata_dir())
    log_path = setup_logging(directory)
    install_excepthook(log_path)
    log.info("%s %s starting", APP_DISPLAY_NAME, __version__)
    window = MainWindow(directory, restore=not args.no_restore)
    window.show()
    if args.project:
        window.open_project(os.path.abspath(args.project), confirm=False)
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
