"""Single-instance guard for the Odysseus desktop shell on Windows."""

import logging
import socket
import sys

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Global\\OdysseusDesktop_v1"
_INSTANCE_LOCK_HOST = "127.0.0.1"
_INSTANCE_LOCK_PORT = 17001
_WINDOW_TITLE = "Odysseus"
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9

_PRIMARY_MUTEX_HANDLE = None
_INSTANCE_LOCK_SOCKET: socket.socket | None = None


def _is_windows() -> bool:
    return sys.platform == "win32"


def _activate_existing_window() -> bool:
    """Try to restore an already running Odysseus window."""
    if not _is_windows():
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, _WINDOW_TITLE)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as exc:
        logger.warning("Не удалось активировать окно Odysseus: %s", exc)
        return False


def _acquire_instance_socket() -> bool:
    """Bind a localhost port for the lifetime of the primary desktop process."""
    global _INSTANCE_LOCK_SOCKET
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((_INSTANCE_LOCK_HOST, _INSTANCE_LOCK_PORT))
        sock.listen(1)
    except OSError:
        sock.close()
        return False
    _INSTANCE_LOCK_SOCKET = sock
    return True


def claim_primary_instance() -> bool:
    """Return True if this process should start; False if another copy is running."""
    global _PRIMARY_MUTEX_HANDLE
    if not _is_windows():
        return _acquire_instance_socket()

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if handle == 0:
            return _acquire_instance_socket()
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            logger.info("Odysseus уже запущен — активируем существующее окно")
            _activate_existing_window()
            return False
        _PRIMARY_MUTEX_HANDLE = handle
        if not _acquire_instance_socket():
            logger.info("Odysseus уже запущен (port lock) — активируем существующее окно")
            _activate_existing_window()
            return False
        return True
    except Exception as exc:
        logger.warning("Single-instance guard недоступен: %s", exc)
        return _acquire_instance_socket()


def ensure_single_desktop_instance() -> bool:
    """Exit early when a desktop instance is already running."""
    return claim_primary_instance()
