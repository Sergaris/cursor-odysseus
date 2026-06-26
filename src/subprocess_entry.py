"""Entry helpers for Odysseus subprocesses in frozen (PyInstaller) builds."""

import os
import sys


def is_mcp_worker_argv(argv: list[str] | None = None) -> bool:
    """True when exe was invoked as ``Odysseus.exe path/to/mcp_servers/foo.py``."""
    args = list(argv if argv is not None else sys.argv)
    if len(args) < 2:
        return False
    script_arg = args[1]
    if not script_arg.lower().endswith(".py"):
        return False
    norm = script_arg.replace("\\", "/")
    return "/mcp_servers/" in f"/{norm.lstrip('/')}" or norm.startswith("mcp_servers/")


def resolve_mcp_worker_script(argv: list[str] | None = None) -> str | None:
    """Return absolute MCP script path from argv or None."""
    args = list(argv if argv is not None else sys.argv)
    if not is_mcp_worker_argv(args):
        return None
    script_path = os.path.abspath(args[1])
    if not os.path.isfile(script_path):
        return None
    return script_path


def run_mcp_worker_if_requested(argv: list[str] | None = None) -> bool:
    """Run MCP server script without starting the desktop shell.

    Returns:
        True if this process handled argv as an MCP worker and exited.
    """
    script_path = resolve_mcp_worker_script(argv)
    if script_path is None:
        return False

    from src.runtime_paths import get_app_root

    base_dir = get_app_root()
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    import runpy

    runpy.run_path(script_path, run_name="__main__")
    sys.exit(0)
