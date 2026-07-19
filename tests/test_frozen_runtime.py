import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from src.frozen_runtime import configure_frozen_runtime, resolve_bundled_bridge_path
from src.runtime_paths import ensure_default_workspace_dir, get_default_workspace_dir, get_install_dir


def test_get_default_workspace_dir_dev():
    with mock.patch.object(sys, "frozen", False, create=True):
        path = get_default_workspace_dir()
        assert os.path.isdir(path)


def test_get_default_workspace_dir_frozen():
    with mock.patch.object(sys, "frozen", True, create=True):
        path = get_default_workspace_dir()
        expected = os.path.join(os.path.expanduser("~"), ".odysseus", "workspace")
        assert path == expected


def test_get_install_dir_frozen():
    mock_exe = os.path.join(os.path.abspath("mock_exe_dir"), "Odysseus.exe")
    with mock.patch.object(sys, "frozen", True, create=True), \
         mock.patch.object(sys, "executable", mock_exe, create=True):
        assert get_install_dir() == os.path.abspath("mock_exe_dir")


def test_resolve_bundled_bridge_path_from_meipass(tmp_path: Path):
    bridge = tmp_path / "cursor_sdk" / "_vendor" / "bridge" / "bin" / "cursor-sdk-bridge.cmd"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("@echo off\r\n", encoding="utf-8")

    with mock.patch.object(sys, "frozen", True, create=True), \
         mock.patch.object(sys, "_MEIPASS", str(tmp_path), create=True):
        assert resolve_bundled_bridge_path() == bridge


def test_configure_frozen_runtime_sets_bridge_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bridge = tmp_path / "cursor_sdk" / "_vendor" / "bridge" / "bin" / "cursor-sdk-bridge.cmd"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.delenv("CURSOR_SDK_BRIDGE_BIN", raising=False)
    scheduled: list[bool] = []
    monkeypatch.setattr(
        "src.bundled_searxng.schedule_bundled_searxng_background",
        lambda: scheduled.append(True),
    )

    with mock.patch.object(sys, "frozen", True, create=True), \
         mock.patch.object(sys, "_MEIPASS", str(tmp_path), create=True):
        configure_frozen_runtime()

    assert os.environ["CURSOR_SDK_BRIDGE_BIN"] == str(bridge)
    assert os.path.isdir(ensure_default_workspace_dir())
    assert scheduled == [True]
