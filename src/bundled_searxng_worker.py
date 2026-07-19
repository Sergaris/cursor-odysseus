"""Subprocess entry: встроенный SearXNG (SimpleXNG) для portable-сборки."""

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from src.uvloop_compat import install_simplexng_platform_stubs


def run_bundled_searxng_worker() -> None:
    install_simplexng_platform_stubs()
    """Запускает SimpleXNG/waitress и блокируется до SIGTERM."""
    port = int(os.environ.get("ODYSSEUS_SEARXNG_PORT", "8888"))
    host = os.environ.get("ODYSSEUS_SEARXNG_HOST", "127.0.0.1").strip() or "127.0.0.1"
    settings_raw = (os.environ.get("ODYSSEUS_SEARXNG_SETTINGS") or "").strip()
    settings_path = Path(settings_raw) if settings_raw else None
    if settings_path is not None and settings_path.is_file():
        os.environ["SEARXNG_SETTINGS_PATH"] = str(settings_path.resolve())
    elif settings_path is not None and not settings_path.is_file():
        settings_path = None

    os.environ["SEARXNG_DISABLE_ETC_SETTINGS"] = "1"

    from simplexng.simplexng import init_settings, log_setup, start_server

    def _handle_sigterm(_signum: int, _frame: object) -> None:
        logging.getLogger("simplexng").warning("SearXNG worker: shutdown")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_sigterm)

    log = log_setup(logging.WARNING)
    init_settings(port=port, host=host, settings_path=settings_path)
    args = argparse.Namespace(
        host=host,
        port=port,
        open=False,
        flask=False,
        verbose=False,
    )
    start_server(args, log)
