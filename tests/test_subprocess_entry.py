import sys

import pytest

from src.subprocess_entry import is_mcp_worker_argv, resolve_mcp_worker_script


def test_is_mcp_worker_argv_detects_mcp_script():
    argv = ["Odysseus.exe", r"C:\app\_internal\mcp_servers\memory_server.py"]
    assert is_mcp_worker_argv(argv) is True


def test_is_mcp_worker_argv_rejects_launcher_only():
    assert is_mcp_worker_argv(["Odysseus.exe"]) is False
    assert is_mcp_worker_argv(["python.exe", "launcher.py"]) is False


def test_resolve_mcp_worker_script_existing(tmp_path):
    script = tmp_path / "mcp_servers" / "email_server.py"
    script.parent.mkdir()
    script.write_text("print('ok')\n", encoding="utf-8")
    argv = ["Odysseus.exe", str(script)]
    assert resolve_mcp_worker_script(argv) == str(script.resolve())


def test_run_mcp_worker_executes_script(tmp_path):
    script = tmp_path / "mcp_servers" / "worker.py"
    script.parent.mkdir()
    script.write_text("import sys\nsys.stdout.write('worker-ok')\n", encoding="utf-8")
    argv = ["Odysseus.exe", str(script)]

    with pytest.raises(SystemExit) as exc:
        from src.subprocess_entry import run_mcp_worker_if_requested

        run_mcp_worker_if_requested(argv)
    assert exc.value.code == 0
