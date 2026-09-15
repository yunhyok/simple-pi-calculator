"""Auto-save manager: debounced writes, instance lock, session state (DESIGN.md §5.8).

The Qt-free file handling lives in :class:`simple_pi_calculator.io.project_io.AutosaveStore`;
this module adds the 1 s debounce timer, the ``QLockFile`` and the conversion between the window
and :class:`~simple_pi_calculator.io.project_io.Session`.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QByteArray,
    QDir,
    QLockFile,
    QObject,
    QSettings,
    QStandardPaths,
    QTimer,
    Signal,
)

from simple_pi_calculator.constants import APPDATA_ENV_VAR
from simple_pi_calculator.io.project_io import AutosaveStore, Session, resolve_appdata_dir

if TYPE_CHECKING:  # pragma: no cover
    from simple_pi_calculator.gui.main_window import MainWindow

log = logging.getLogger(__name__)

LOCK_FILE = "autosave.lock"
FAILURE_MESSAGE_INTERVAL_S = 60.0


def appdata_dir() -> str:
    """Auto-save directory (§5.8.1): ``$SPICAL_APPDATA_DIR`` or Qt ``AppDataLocation``."""
    if os.environ.get(APPDATA_ENV_VAR):
        return resolve_appdata_dir()
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return resolve_appdata_dir(location or None)


def ensure_dir(path: str) -> str:
    QDir().mkpath(path)
    return path


def b64_from_qbytearray(data: QByteArray) -> str:
    return bytes(data.toBase64().data()).decode("ascii")


def qbytearray_from_b64(text: str) -> QByteArray:
    return QByteArray.fromBase64(QByteArray(text.encode("ascii")))


def app_settings(directory: str | None = None) -> QSettings:
    """QSettings for window geometry / splitter state and view options.

    With ``$SPICAL_APPDATA_DIR`` (tests, portable use) an INI file inside that folder is used so
    nothing leaks into the user profile; otherwise the platform default store.
    """
    override = directory or os.environ.get(APPDATA_ENV_VAR)
    if override:
        return QSettings(os.path.join(override, "settings.ini"), QSettings.Format.IniFormat)
    return QSettings()


class AutosaveManager(QObject):
    """Debounced auto-save of the complete application state (§5.8.2)."""

    DEBOUNCE_MS = 1000
    status = Signal(str)

    def __init__(self, store: AutosaveStore, window: "MainWindow"):
        super().__init__(window)
        self.store = store
        self.window = window
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self.flush)
        self._lock = QLockFile(os.path.join(store.directory, LOCK_FILE))
        self._lock.setStaleLockTime(30000)
        self._locked = False
        self._suspended = 0
        self._last_failure_msg = 0.0
        self.write_count = 0

    # -- lock -------------------------------------------------------------------------------------
    def acquire_lock(self) -> bool:
        if self._locked:
            return True
        ok = self._lock.tryLock(200)
        if not ok and self._lock.error() == QLockFile.LockError.LockFailedError:
            if self._lock.removeStaleLockFile():
                ok = self._lock.tryLock(200)
        self._locked = bool(ok)
        return self._locked

    def release_lock(self) -> None:
        self._timer.stop()
        if self._locked:
            self._lock.unlock()
            self._locked = False

    @property
    def enabled(self) -> bool:
        return self._locked

    # -- scheduling -------------------------------------------------------------------------------
    def suspend(self) -> "_Suspend":
        """Context manager: no scheduling while applying restored state (§5.8.3 step 6)."""
        return _Suspend(self)

    @property
    def suspended(self) -> bool:
        return self._suspended > 0

    def schedule(self) -> None:
        if not self.enabled or self._suspended:
            return
        self._timer.start()

    def is_pending(self) -> bool:
        return self._timer.isActive()

    def flush(self) -> None:
        self._timer.stop()
        if not self.enabled:
            return
        try:
            session = self.collect_session()
            if self.store.save(self.window.project, session):
                self.write_count += 1
        except OSError as exc:
            log.warning("Auto-save failed: %s", exc)
            now = time.monotonic()
            if now - self._last_failure_msg >= FAILURE_MESSAGE_INTERVAL_S:
                self._last_failure_msg = now
                self.status.emit(f"Auto-save failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - never crash the GUI because of auto-save
            log.exception("Auto-save failed")
            self.status.emit(f"Auto-save failed: {exc}")

    # -- session ----------------------------------------------------------------------------------
    def collect_session(self) -> Session:
        return self.window.collect_session()

    def apply_session(self, session: Session) -> None:
        with self.suspend():
            self.window.apply_session(session)


class _Suspend:
    def __init__(self, manager: AutosaveManager):
        self.manager = manager

    def __enter__(self) -> AutosaveManager:
        self.manager._suspended += 1
        return self.manager

    def __exit__(self, *exc) -> None:
        self.manager._suspended -= 1


__all__ = ["AutosaveManager", "appdata_dir", "app_settings", "ensure_dir",
           "b64_from_qbytearray", "qbytearray_from_b64"]
