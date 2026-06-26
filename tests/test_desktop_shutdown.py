"""Tests for desktop shutdown helpers."""

from unittest.mock import patch

from src.desktop_shutdown import (
    _is_descendant,
    collect_straggler_pids,
    get_process_cleanup_markers,
    is_shutdown_done,
)


def test_is_descendant():
    parent_map = {100: 1, 200: 100, 300: 200, 999: 500}
    assert _is_descendant(200, 1, parent_map) is True
    assert _is_descendant(300, 1, parent_map) is True
    assert _is_descendant(999, 1, parent_map) is False


def test_collect_straggler_pids_filters_by_marker_and_tree(monkeypatch):
    root = 1000
    marker = get_process_cleanup_markers()[0]
    processes = [
        {"ProcessId": root, "ParentProcessId": 1, "CommandLine": f"{marker}\\app.py"},
        {"ProcessId": 2001, "ParentProcessId": root, "CommandLine": f"{marker}\\mcp_servers\\x.py"},
        {"ProcessId": 2002, "ParentProcessId": root, "CommandLine": "C:\\Windows\\System32\\cmd.exe"},
        {"ProcessId": 2003, "ParentProcessId": 5000, "CommandLine": f"{marker}\\other.py"},
        {"ProcessId": 2004, "ParentProcessId": root, "CommandLine": "msedgewebview2.exe --embedded"},
    ]
    monkeypatch.setattr("src.desktop_shutdown._list_win32_processes", lambda: processes)
    pids = collect_straggler_pids(root, (marker,))
    assert pids == {2001, 2002, 2004}


def test_collect_straggler_pids_includes_orphan_bridge(monkeypatch):
    root = 1000
    marker = get_process_cleanup_markers()[0]
    bridge_cmd = f"{marker}\\_internal\\cursor_sdk\\_vendor\\bridge\\bin\\node.exe"
    processes = [
        {"ProcessId": root, "ParentProcessId": 1, "CommandLine": f"{marker}\\Odysseus.exe"},
        {"ProcessId": 3001, "ParentProcessId": 1, "CommandLine": bridge_cmd},
    ]
    monkeypatch.setattr("src.desktop_shutdown._list_win32_processes", lambda: processes)
    pids = collect_straggler_pids(root, (marker,))
    assert pids == {3001}


def test_is_shutdown_done_initially_false():
    assert is_shutdown_done() is False
