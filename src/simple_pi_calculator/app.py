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
                        help="check modules, bundled resources and the example computation, "
                             "then exit (0 = OK)")
    parser.add_argument("--self-test-report", metavar="PATH",
                        help="also write the self-test report to PATH (windowed builds have no "
                             "console)")
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


def _find_main_window():
    """The running :class:`MainWindow`, if any (no import of the GUI package at module level)."""
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return None
        for widget in app.topLevelWidgets():
            if widget.objectName() == "MainWindow" and hasattr(widget, "report_internal_error"):
                return widget
    except Exception:  # noqa: BLE001
        return None
    return None


def make_excepthook(log_path: str | None, window_getter=_find_main_window):
    """Hook for uncaught exceptions, including exceptions raised inside Qt slots (§5.7).

    The traceback always goes to the log file. When a main window exists the error is reported
    **non-modally** in its Messages dock (code ``E_INTERNAL``) and status bar: a modal box from
    inside a failing paint/timer slot could re-trigger the failure and stack dialogs. Only when
    no window exists (start-up) is the error written to the default hook (stderr).
    The application keeps running; re-entrant calls are ignored.
    """
    state = {"busy": False}

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Uncaught exception:\n%s", text)
        if state["busy"] or issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        state["busy"] = True
        try:
            window = window_getter()
            if window is not None:
                window.report_internal_error(f"{exc_type.__name__}: {exc}", text, log_path)
                return
        except Exception:  # noqa: BLE001 - the hook itself must never raise
            log.exception("Error while reporting an uncaught exception")
        finally:
            state["busy"] = False
        sys.__excepthook__(exc_type, exc, tb)

    return hook


def install_excepthook(log_path: str | None) -> None:
    """Install :func:`make_excepthook` for the main thread and for Python threads."""
    hook = make_excepthook(log_path)
    sys.excepthook = hook

    def thread_hook(args):  # worker threads never touch widgets: log only
        if args.exc_type is SystemExit:
            return
        log.error("Uncaught exception in thread %s:\n%s",
                  getattr(args.thread, "name", "?"),
                  "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                     args.exc_traceback)))

    import threading
    threading.excepthook = thread_hook


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


def _self_test_checks(app, lines: list[str]) -> None:
    """Body of :func:`run_self_test`; raises on the first failed check."""
    import math

    from PySide6.QtCore import QTimer

    from simple_pi_calculator.gui.help_window import HELP_PAGES, HelpWindow, help_dir
    from simple_pi_calculator.gui.main_window import MainWindow, examples_dir

    modules = import_all_modules()
    lines.append(f"modules imported: {len(modules)}")
    if getattr(sys, "frozen", False):
        lines.append(f"frozen bundle: {getattr(sys, '_MEIPASS', '?')}")

    # packaged resources (help pages + images, icon, examples) resolve to real files
    folder = help_dir()
    lines.append(f"help folder: {folder}")
    if not os.path.isfile(os.path.join(folder, "index.html")):
        raise RuntimeError(f"help index.html not found in {folder}")
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "app.png")
    if not os.path.isfile(icon):
        raise RuntimeError(f"application icon not found: {icon}")
    help_win = HelpWindow()
    missing: list[str] = []
    for file_name, _title in HELP_PAGES:
        if not os.path.isfile(os.path.join(folder, file_name)):
            missing.append(file_name)
            continue
        help_win.show_page(file_name)
        missing.extend(f"{file_name}: {img}" for img in help_win.missing_images())
    if missing:
        raise RuntimeError("help pages/images missing: " + ", ".join(missing))
    lines.append(f"help pages checked: {len(HELP_PAGES)}")

    ex = examples_dir()
    if ex is None:
        raise RuntimeError("bundled examples folder not found")
    lines.append(f"examples folder: {ex}")

    # headless engine run of the bundled example project (§7.4 step 6)
    from simple_pi_calculator.core.engine import compute_project
    from simple_pi_calculator.errors import Severity
    from simple_pi_calculator.io.project_io import load_project, to_inputs
    path = os.path.join(ex, "example_project.spical.json")
    project, _ = load_project(path)
    project.sweep.n_points = 60  # keep the smoke test fast
    results, issues = compute_project(to_inputs(project, path))
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors or len(results) != len(project.pwr_rows):
        raise RuntimeError("example computation failed: "
                           + "; ".join(f"{i.code}: {i.message}" for i in errors))
    for res in results:
        values = [abs(complex(z)) for z in res.marker_z]
        if not values or not all(math.isfinite(v) and v > 0 for v in values):
            raise RuntimeError(f"{res.name}: invalid marker values {values}")
        lines.append(f"{res.name}: |Z| @ 1/10/100 MHz = "
                     + ", ".join(f"{v * 1e3:.4g}" for v in values) + " mOhm")

    window = MainWindow(restore=False, autosave=False, use_settings=False, auto_compute=False)
    window.show()
    QTimer.singleShot(0, app.quit)
    app.exec()
    help_win.close()
    window.close()


def run_self_test(app, report_path: str | None = None) -> int:
    """Packaged-build smoke test (§7.4 step 6, §8.12). Never touches the auto-save folder.

    Imports every module, checks the bundled help pages/images, icon and examples, computes the
    example project headless and creates the main window. Prints ``SELF-TEST OK`` (or the failure)
    and, with ``report_path``, also writes the report to that file — a windowed PyInstaller build
    has no console, so CI reads the file and relies on the exit code.
    """
    lines = [f"{APP_DISPLAY_NAME} {__version__} self-test"]
    try:
        _self_test_checks(app, lines)
        lines.append("SELF-TEST OK")
        code = 0
    except Exception:  # noqa: BLE001
        lines.append(traceback.format_exc())
        lines.append("SELF-TEST FAILED")
        code = 1
    text = "\n".join(lines)
    if sys.stdout is not None:  # None in a windowed (console=False) build
        print(text)
    if report_path:
        try:
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            code = code or 2
    return code


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
        return run_self_test(app, args.self_test_report)

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
