import sys
from unittest import mock

from src.desktop_single_instance import (
    _activate_existing_window,
    claim_primary_instance,
    ensure_single_desktop_instance,
)


def test_claim_primary_instance_non_windows():
    with mock.patch.object(sys, "platform", "linux"):
        assert claim_primary_instance() is True


def test_claim_primary_instance_first_launch():
    mock_kernel32 = mock.Mock()
    mock_kernel32.CreateMutexW.return_value = 1
    mock_kernel32.GetLastError.return_value = 0
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch("ctypes.windll.kernel32", mock_kernel32, create=True), \
         mock.patch("src.desktop_single_instance._acquire_instance_socket", return_value=True):
        assert claim_primary_instance() is True


def test_claim_primary_instance_second_launch_activates_window():
    mock_kernel32 = mock.Mock()
    mock_kernel32.CreateMutexW.return_value = 1
    mock_kernel32.GetLastError.return_value = 183
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch("ctypes.windll.kernel32", mock_kernel32, create=True), \
         mock.patch("src.desktop_single_instance._activate_existing_window", return_value=True) as activate:
        assert claim_primary_instance() is False
        activate.assert_called_once()


def test_ensure_single_desktop_instance():
    with mock.patch("src.desktop_single_instance.claim_primary_instance", return_value=False):
        assert ensure_single_desktop_instance() is False


def test_activate_existing_window_finds_title():
    mock_user32 = mock.Mock()
    mock_user32.FindWindowW.return_value = 42
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch("ctypes.windll.user32", mock_user32, create=True):
        assert _activate_existing_window() is True
        mock_user32.ShowWindow.assert_called_once()
        mock_user32.SetForegroundWindow.assert_called_once()
