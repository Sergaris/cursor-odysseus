# tests/test_launcher.py
import os
from unittest import mock

import pytest

import launcher
from launcher import NullWriter, close_splash, create_tray_image, on_exit, on_show_ui, open_browser


@pytest.fixture(autouse=True)
def _reset_show_ui_debounce():
    launcher._last_show_ui_at = 0.0
    yield
    launcher._last_show_ui_at = 0.0


def test_null_writer():
    writer = NullWriter()
    writer.write("hello")
    writer.flush()
    assert writer.isatty() is False
    assert isinstance(writer.fileno(), int)


def test_create_tray_image():
    try:
        from PIL import Image
        img = create_tray_image()
        assert isinstance(img, Image.Image)
        assert img.size == (64, 64)
    except ImportError:
        pytest.skip("Pillow/PIL not installed in test environment")


def test_on_show_ui_prefers_window():
    window = mock.Mock()
    icon_mock = mock.Mock()
    item_mock = mock.Mock()
    url = "http://127.0.0.1:7000"
    with mock.patch("launcher.time") as mock_time:
        mock_time.monotonic.return_value = 10.0
        on_show_ui(icon_mock, item_mock, url, window)
    window.show.assert_called_once()
    window.restore.assert_called_once()


def test_on_show_ui_falls_back_to_browser():
    with mock.patch("launcher.open_external_browser") as mock_open, \
         mock.patch("launcher.time") as mock_time:
        mock_time.monotonic.return_value = 10.0
        icon_mock = mock.Mock()
        item_mock = mock.Mock()
        url = "http://127.0.0.1:7000"
        on_show_ui(icon_mock, item_mock, url, None)
        mock_open.assert_called_once_with(url)


def test_on_show_ui_does_not_open_browser_when_window_exists():
    with mock.patch("launcher.open_external_browser") as mock_open, \
         mock.patch("launcher.time") as mock_time:
        mock_time.monotonic.return_value = 10.0
        window = mock.Mock()
        window.show.side_effect = RuntimeError("boom")
        on_show_ui(mock.Mock(), mock.Mock(), "http://127.0.0.1:7000", window)
        mock_open.assert_not_called()


def test_on_show_ui_debounced():
    with mock.patch("launcher.open_external_browser") as mock_open, \
         mock.patch("launcher.time") as mock_time:
        mock_time.monotonic.side_effect = [10.0, 10.1, 11.0]
        url = "http://127.0.0.1:7000"
        on_show_ui(mock.Mock(), mock.Mock(), url, None)
        on_show_ui(mock.Mock(), mock.Mock(), url, None)
        on_show_ui(mock.Mock(), mock.Mock(), url, None)
        assert mock_open.call_count == 2


def test_on_exit():
    with mock.patch("launcher._shutdown_desktop_app") as mock_shutdown:
        icon_mock = mock.Mock()
        item_mock = mock.Mock()
        on_exit(icon_mock, item_mock)
        mock_shutdown.assert_called_once()


def test_open_browser():
    with mock.patch("launcher.open_external_browser") as mock_open, \
         mock.patch("time.sleep") as mock_sleep:

        with mock.patch("launcher.splash_root", None):
            open_browser("http://127.0.0.1:7000")
            mock_open.assert_called_once_with("http://127.0.0.1:7000")
            mock_sleep.assert_called_once_with(3.5)

    with mock.patch("launcher.open_external_browser") as mock_open, \
         mock.patch("time.sleep"):
        mock_splash = mock.Mock()
        with mock.patch("launcher.splash_root", mock_splash):
            open_browser("http://127.0.0.1:7000")
            mock_splash.after.assert_called_once()


def test_close_splash_noop_when_missing():
    with mock.patch("launcher.splash_root", None):
        close_splash()
