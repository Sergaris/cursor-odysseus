"""Tests for desktop shutdown helpers."""

from src.desktop_shutdown import _is_descendant, collect_straggler_pids, get_process_cleanup_markers


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
    ]
    monkeypatch.setattr("src.desktop_shutdown._list_win32_processes", lambda: processes)
    pids = collect_straggler_pids(root, (marker,))
    assert pids == {2001}
