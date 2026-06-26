import asyncio
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from src import windows_subprocess as ws


def test_resolve_hidden_bridge_argv_from_cmd(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cmd = bin_dir / "cursor-sdk-bridge.cmd"
    cmd.write_text("@echo off\r\n", encoding="utf-8")
    node = bin_dir / "node.exe"
    node.write_bytes(b"")
    bridge_js = tmp_path / "dist" / "bin" / "cursor-sdk-bridge.js"
    bridge_js.parent.mkdir(parents=True)
    bridge_js.write_text("// bridge", encoding="utf-8")

    with mock.patch.object(sys, "platform", "win32"):
        argv = ws.resolve_hidden_bridge_argv(cmd)

    assert argv == [str(node), str(bridge_js)]


def test_resolve_hidden_bridge_argv_skips_non_windows():
    with mock.patch.object(sys, "platform", "linux"):
        assert ws.resolve_hidden_bridge_argv("/tmp/bridge.cmd") is None


@pytest.mark.asyncio
async def test_install_subprocess_hide_console_sets_creationflags():
    ws._SUBPROCESS_PATCHED = False
    calls: list[int] = []

    async def fake_exec(*args, **kwargs):
        calls.append(kwargs.get("creationflags", 0))
        proc = mock.Mock()
        proc.returncode = None
        return proc

    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch.object(asyncio, "create_subprocess_exec", fake_exec), \
         mock.patch.object(asyncio, "create_subprocess_shell", fake_exec):
        ws.install_subprocess_hide_console()
        await asyncio.create_subprocess_exec("echo", "hi")

    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert calls == [expected]
    ws._SUBPROCESS_PATCHED = False


def test_rewrite_bridge_command_uses_hidden_argv():
    node = r"C:\bin\node.exe"
    js = r"C:\dist\cursor-sdk-bridge.js"
    with mock.patch.object(ws, "resolve_hidden_bridge_argv", return_value=[node, js]):
        assert ws._rewrite_bridge_command(None) == [node, js]
        assert ws._rewrite_bridge_command(r"C:\bridge.cmd") == [node, js]
        assert ws._rewrite_bridge_command([node, js]) == [node, js]
