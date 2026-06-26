"""Native desktop window shell for Odysseus (WebView2 via pywebview on Windows)."""

import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_WIDTH = 1280
DEFAULT_WINDOW_HEIGHT = 840
DEFAULT_WINDOW_MIN_WIDTH = 960
DEFAULT_WINDOW_MIN_HEIGHT = 640
SERVER_READY_TIMEOUT_SEC = 90.0
SERVER_POLL_INTERVAL_SEC = 0.25


def should_use_desktop_shell() -> bool:
    """Return whether the app should open in a native window instead of a browser."""
    override = os.getenv("ODYSSEUS_DESKTOP", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return getattr(sys, "frozen", False)


def wait_for_http_server(url: str, *, timeout_sec: float = SERVER_READY_TIMEOUT_SEC) -> None:
    """Block until the HTTP server responds or raise TimeoutError."""
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
            last_error = exc
        except OSError as exc:
            last_error = exc
        time.sleep(SERVER_POLL_INTERVAL_SEC)

    detail = f": {last_error}" if last_error else ""
    raise TimeoutError(f"сервер не ответил за {timeout_sec:.0f} с ({url}){detail}")


def _configure_webview_settings(webview: Any) -> None:
    """Keep target=_blank / window.open inside the desktop WebView when possible."""
    try:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    except Exception:
        logger.exception("Не удалось применить настройки pywebview")


def _create_odysseus_window(webview: Any, *, url: str) -> Any:
    """Create the main Odysseus WebView window."""
    return webview.create_window(
        title="Odysseus",
        url=url,
        width=DEFAULT_WINDOW_WIDTH,
        height=DEFAULT_WINDOW_HEIGHT,
        min_size=(DEFAULT_WINDOW_MIN_WIDTH, DEFAULT_WINDOW_MIN_HEIGHT),
        background_color="#1a1c23",
        text_select=True,
    )


def _import_webview() -> Any:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "pywebview не установлен. Для desktop-режима: pip install pywebview"
        ) from exc
    return webview


def open_external_browser(url: str) -> None:
    """Open Odysseus in the default system browser."""
    webbrowser.open(url)


def run_desktop_window(
    *,
    url: str,
    on_window_closed: Callable[[], None] | None = None,
) -> None:
    """Open Odysseus in a native WebView window. Blocks until the window closes."""
    webview = _import_webview()
    _configure_webview_settings(webview)
    window = _create_odysseus_window(webview, url=url)

    def _handle_closed() -> None:
        if on_window_closed is not None:
            on_window_closed()

    window.events.closed += _handle_closed

    def _focus_window() -> None:
        try:
            window.show()
            window.restore()
        except Exception:
            logger.exception("Не удалось активировать окно Odysseus")

    window.events.loaded += _focus_window
    gui = "edgechromium" if sys.platform == "win32" else None
    if gui is not None:
        webview.start(gui=gui)
    else:
        webview.start()


def run_desktop_app(
    *,
    url: str,
    start_server: Callable[[], None],
    close_splash: Callable[[], None],
    setup_system_tray: Callable[[str, Any | None], None],
    shutdown_app: Callable[[], None] | None = None,
) -> None:
    """Start the backend, wait for readiness, then show the native desktop shell."""
    server_error: list[BaseException] = []

    def _server_thread() -> None:
        try:
            start_server()
        except BaseException as exc:
            server_error.append(exc)
            logger.exception("Ошибка фонового сервера Odysseus")

    threading.Thread(target=_server_thread, daemon=True, name="odysseus-server").start()

    try:
        wait_for_http_server(url)
    except TimeoutError:
        close_splash()
        if server_error:
            raise server_error[0]
        raise

    close_splash()

    webview = _import_webview()
    _configure_webview_settings(webview)
    window = _create_odysseus_window(webview, url=url)
    threading.Thread(
        target=setup_system_tray,
        args=(url, window),
        daemon=True,
        name="odysseus-tray",
    ).start()

    def _focus_window() -> None:
        try:
            window.show()
            window.restore()
        except Exception:
            logger.exception("Не удалось активировать окно Odysseus")

    def _request_shutdown() -> None:
        nonlocal _shutdown_requested
        if _shutdown_requested:
            return
        _shutdown_requested = True
        if shutdown_app is not None:
            shutdown_app()
        else:
            os._exit(0)

    def _on_closing() -> bool:
        _request_shutdown()
        return True

    _shutdown_requested = False

    if hasattr(window.events, "closing"):
        window.events.closing += _on_closing
    window.events.closed += _request_shutdown

    _window_focused = False

    def _focus_window_once() -> None:
        nonlocal _window_focused
        if _window_focused:
            return
        _window_focused = True
        _focus_window()

    window.events.loaded += _focus_window_once
    gui = "edgechromium" if sys.platform == "win32" else None
    try:
        if gui is not None:
            webview.start(gui=gui)
        else:
            webview.start()
    finally:
        if not _shutdown_requested:
            logger.info("Окно закрыто — выполняем shutdown")
            if shutdown_app is not None:
                shutdown_app()
            else:
                os._exit(0)

    if server_error:
        raise server_error[0]
