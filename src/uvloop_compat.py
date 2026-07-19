"""Платформенные заглушки для SimpleXNG на Windows."""

import os
import sys
import types
from dataclasses import dataclass


def install_simplexng_platform_stubs() -> None:
    """Подставляет модули Unix-only до импорта SimpleXNG/SearX."""
    if sys.platform != "win32":
        return
    _install_uvloop_stub()
    _install_pwd_stub()
    _install_multiprocessing_fork_stub()


def install_uvloop_stub() -> None:
    """Совместимость со старыми импортами."""
    install_simplexng_platform_stubs()


def _install_uvloop_stub() -> None:
    if "uvloop" in sys.modules:
        return
    stub = types.ModuleType("uvloop")
    stub.install = lambda: None
    sys.modules["uvloop"] = stub


def _install_pwd_stub() -> None:
    if "pwd" in sys.modules:
        return

    @dataclass(frozen=True, slots=True)
    class _Passwd:
        pw_name: str
        pw_uid: int

    stub = types.ModuleType("pwd")
    stub.getpwuid = lambda _uid: _Passwd(pw_name=os.environ.get("USERNAME", "user"), pw_uid=0)
    sys.modules["pwd"] = stub


def _install_multiprocessing_fork_stub() -> None:
    """SearX calculator plugin запрашивает fork — на Windows подменяем на spawn."""
    import multiprocessing

    if getattr(multiprocessing, "_odysseus_fork_patched", False):
        return
    original = multiprocessing.get_context

    def get_context(method: str | None = None):
        if method == "fork":
            method = "spawn"
        return original(method)

    multiprocessing.get_context = get_context  # type: ignore[method-assign]
    multiprocessing._odysseus_fork_patched = True  # type: ignore[attr-defined]
