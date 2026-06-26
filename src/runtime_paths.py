"""Helpers for resolving runtime paths in source and frozen builds."""

import os
import sys


def get_app_root() -> str:
    """Return the app root directory.

    In normal source runs, this is the repository root. In a frozen Windows
    build, it is the bundle content root (PyInstaller's internal directory)
    so bundled runtime folders like `static/`, `scripts/`, and `data/` stay
    together with the executable payload.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_default_data_dir() -> str:
    """Return the default path to the data directory.

    In normal runs, this is a 'data' subdirectory under the app root.
    In frozen builds, it is a persistent user directory (~/.odysseus/data)
    to prevent SQLite databases and other persistent files from being
    written to the ephemeral, temporary extraction directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".odysseus", "data")
    return os.path.join(get_app_root(), "data")


def get_install_dir() -> str:
    """Return the directory that contains Odysseus.exe (not PyInstaller _MEIPASS)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return get_app_root()


def get_default_workspace_dir() -> str:
    """Return the default Cursor SDK / agent workspace directory."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".odysseus", "workspace")
    return get_app_root()


def ensure_default_workspace_dir() -> str:
    """Creates the default workspace directory if missing."""
    workspace = get_default_workspace_dir()
    os.makedirs(workspace, exist_ok=True)
    return workspace