# tests/test_desktop_shell.py
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest import mock

import pytest

from desktop_shell import (
    _configure_webview_settings,
    open_external_browser,
    run_desktop_app,
    should_use_desktop_shell,
    wait_for_http_server,
)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args) -> None:
        return


def test_should_use_desktop_shell_env_override():
    with mock.patch.dict(os.environ, {"ODYSSEUS_DESKTOP": "1"}, clear=False):
        assert should_use_desktop_shell() is True
    with mock.patch.dict(os.environ, {"ODYSSEUS_DESKTOP": "0"}, clear=False):
        with mock.patch.object(sys, "frozen", False, create=True):
            assert should_use_desktop_shell() is False


def test_should_use_desktop_shell_frozen_default():
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(sys, "frozen", True, create=True):
            assert should_use_desktop_shell() is True
        with mock.patch.object(sys, "frozen", False, create=True):
            assert should_use_desktop_shell() is False


def test_wait_for_http_server_success():
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_for_http_server(f"http://{host}:{port}/", timeout_sec=5.0)
    finally:
        server.shutdown()


def test_wait_for_http_server_timeout():
    with pytest.raises(TimeoutError):
        wait_for_http_server("http://127.0.0.1:1/", timeout_sec=0.5)


def test_open_external_browser():
    with mock.patch("desktop_shell.webbrowser.open") as mock_open:
        open_external_browser("http://127.0.0.1:7000")
        mock_open.assert_called_once_with("http://127.0.0.1:7000")


def test_configure_webview_settings_disables_external_browser():
    webview = mock.Mock()
    webview.settings = {}
    _configure_webview_settings(webview)
    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is False


def test_run_desktop_app_starts_tray_in_background_before_webview():
    class _EventHandlers(list):
        def __iadd__(self, handler):
            self.append(handler)
            return self

    mock_window = mock.Mock()
    mock_window.events = SimpleNamespace(
        closed=_EventHandlers(),
        loaded=_EventHandlers(),
        closing=_EventHandlers(),
    )
    mock_webview = mock.Mock()
    mock_webview.create_window.return_value = mock_window
    tray_calls: list[tuple] = []
    webview_started = threading.Event()
    shutdown_calls: list[str] = []

    def fake_tray(url: str, window) -> None:
        tray_calls.append((url, window))

    def fake_shutdown() -> None:
        shutdown_calls.append("shutdown")

    def fake_start(*, gui=None) -> None:
        webview_started.set()

    mock_webview.start.side_effect = fake_start

    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    host, port = server.server_address
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        real_thread = threading.Thread
        with mock.patch("desktop_shell._import_webview", return_value=mock_webview), \
             mock.patch("desktop_shell.threading.Thread", side_effect=real_thread), \
             mock.patch("src.desktop_shutdown.is_shutdown_done", return_value=False):
            run_desktop_app(
                url=f"http://{host}:{port}/",
                start_server=lambda: None,
                close_splash=lambda: None,
                setup_system_tray=fake_tray,
                shutdown_app=fake_shutdown,
            )
    finally:
        server.shutdown()

    assert tray_calls
    assert webview_started.is_set()
    mock_webview.start.assert_called_once()
    assert shutdown_calls == ["shutdown"]
