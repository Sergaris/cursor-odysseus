"""Встроенный SearXNG (SimpleXNG) для portable-сборки без Docker."""

import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import yaml

from src.runtime_paths import get_app_root, get_default_data_dir

logger = logging.getLogger(__name__)

SEARXNG_WORKER_ENV = "ODYSSEUS_SEARXNG_WORKER"
BUNDLED_SEARXNG_URL_ENV = "ODYSSEUS_BUNDLED_SEARXNG_URL"
_PORT_ENV = "ODYSSEUS_SEARXNG_PORT"
_HOST_ENV = "ODYSSEUS_SEARXNG_HOST"
_SETTINGS_ENV = "ODYSSEUS_SEARXNG_SETTINGS"

_DEFAULT_HOST = "127.0.0.1"
_PORT_CANDIDATES = (8888, 8081, 8082, 9080, 9876)
_STARTUP_TIMEOUT_SEC = 120.0
_LISTENER_TIMEOUT_SEC = 45.0
_HEALTH_POLL_SEC = 0.5
_PORT_PROBE_TIMEOUT_SEC = 0.2
_PORTABLE_ENGINE_KEEP = ("duckduckgo", "wikipedia", "brave")

_process: subprocess.Popen[bytes] | None = None
_instance_url: str = ""
_probe_finished: bool = False
_bg_thread: threading.Thread | None = None
_worker_stderr_file: object | None = None


def is_searxng_worker_process() -> bool:
    """True, если этот процесс — воркер SearXNG (не GUI Odysseus)."""
    return os.environ.get(SEARXNG_WORKER_ENV) == "1"


def is_bundled_probe_finished() -> bool:
    """True после первого полного цикла старта/проверки bundled SearXNG."""
    return _probe_finished


def mark_bundled_probe_finished(url: str = "") -> None:
    """Фиксирует результат startup-probe, чтобы не сканировать порты повторно."""
    global _probe_finished, _instance_url

    _probe_finished = True
    if url:
        _instance_url = url.rstrip("/")
        os.environ[BUNDLED_SEARXNG_URL_ENV] = _instance_url
        os.environ["SEARXNG_INSTANCE"] = _instance_url


def reset_bundled_probe_state() -> None:
    """Сбрасывает probe-состояние (только для тестов)."""
    global _probe_finished, _bg_thread

    _probe_finished = False
    _bg_thread = None


def is_simplexng_available() -> bool:
    """Проверяет, что SimpleXNG установлен в текущем окружении."""
    try:
        import simplexng  # noqa: F401

        return True
    except ImportError:
        return False


def get_bundled_searxng_url() -> str:
    """URL запущенного bundled SearXNG или пустая строка."""
    return (os.environ.get(BUNDLED_SEARXNG_URL_ENV) or _instance_url or "").strip().rstrip("/")


def _httpx_timeout(total: float) -> httpx.Timeout:
    connect = min(0.5, total)
    return httpx.Timeout(connect=connect, read=total, write=total, pool=connect)


def discover_running_searxng_url() -> str:
    """Ищет уже работающий SearXNG на стандартных локальных портах."""
    for port in _PORT_CANDIDATES:
        if not _port_has_listener(_DEFAULT_HOST, port):
            continue
        url = f"http://{_DEFAULT_HOST}:{port}"
        if _searxng_healthy(url, timeout=1.0):
            return url
    return ""


def _discover_and_adopt_bundled_url(*, adopt: bool = True) -> str:
    """Сканирует порты и при необходимости сохраняет найденный URL."""
    global _instance_url

    discovered = discover_running_searxng_url()
    if not discovered:
        return get_bundled_searxng_url()

    if adopt:
        _instance_url = discovered
        os.environ[BUNDLED_SEARXNG_URL_ENV] = discovered
        os.environ["SEARXNG_INSTANCE"] = discovered
        logger.info("Обнаружен работающий SearXNG: %s", discovered)
    return discovered


def resolve_bundled_searxng_url(*, adopt: bool = True) -> str:
    """Возвращает рабочий URL bundled SearXNG без повторного сканирования портов."""
    existing = get_bundled_searxng_url()
    if existing and _searxng_healthy(existing, timeout=1.0):
        return existing

    if not is_bundled_probe_finished():
        return ""

    return _discover_and_adopt_bundled_url(adopt=adopt)


def retry_bundled_searxng_startup() -> str:
    """Повторяет старт SearXNG, если фоновая попытка не удалась."""
    global _probe_finished

    existing = get_bundled_searxng_url()
    if existing and _searxng_healthy(existing, timeout=1.0):
        return existing

    _probe_finished = False
    return ensure_bundled_searxng_for_search()


def ensure_bundled_searxng_for_search() -> str:
    """Стартует sidecar или подхватывает уже запущенный SearXNG. Возвращает base URL."""
    if is_bundled_probe_finished():
        return get_bundled_searxng_url()

    url = ""
    try:
        if bundled_searxng_enabled():
            start_bundled_searxng()
        url = _discover_and_adopt_bundled_url(adopt=True)
        if not url:
            url = get_bundled_searxng_url()
    finally:
        mark_bundled_probe_finished(url)
    return url


def schedule_bundled_searxng_background() -> None:
    """Запускает bundled SearXNG в фоне, не блокируя старт GUI."""
    global _bg_thread

    if not bundled_searxng_enabled():
        mark_bundled_probe_finished("")
        return
    if is_bundled_probe_finished():
        return
    if _bg_thread is not None and _bg_thread.is_alive():
        return

    def _run() -> None:
        try:
            from src.settings import _invalidate_caches, persist_portable_search_defaults

            url = ensure_bundled_searxng_for_search()
            if url:
                persist_portable_search_defaults()
            else:
                persist_portable_search_defaults(fallback_only=True)
            _invalidate_caches()
        except Exception:
            logger.exception("Фоновый старт bundled SearXNG не удался")
            mark_bundled_probe_finished("")

    _bg_thread = threading.Thread(target=_run, daemon=True, name="bundled-searxng")
    _bg_thread.start()


def bundled_searxng_enabled() -> bool:
    """Нужно ли поднимать bundled SearXNG в этом процессе."""
    from src.frozen_runtime import is_frozen

    if is_frozen():
        return True
    return os.environ.get("ODYSSEUS_DEV_BUNDLED_SEARXNG", "").strip() in ("1", "true", "yes")


def _creationflags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _port_has_listener(host: str, port: int) -> bool:
    """True, если на порту уже отвечает TCP-сервис."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(_PORT_PROBE_TIMEOUT_SEC)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _is_port_free(host: str, port: int) -> bool:
    if _port_has_listener(host, port):
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def _searxng_healthy(base_url: str, *, timeout: float = 5.0) -> bool:
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": "odysseus-health", "format": "json"},
            timeout=_httpx_timeout(timeout),
        )
        return response.status_code == 200 and '"results"' in response.text
    except Exception:
        return False


def _pick_port(host: str) -> int:
    for port in _PORT_CANDIDATES:
        if _port_has_listener(host, port):
            url = f"http://{host}:{port}"
            if _searxng_healthy(url, timeout=1.0):
                return port
            logger.debug("Порт %s занят не-SearXNG сервисом, пропуск", port)
            continue
        if _is_port_free(host, port):
            return port
    raise RuntimeError(f"нет свободного порта для SearXNG среди {_PORT_CANDIDATES}")


_DEFAULT_SECRET_KEYS = frozenset({"ultrasecretkey", "change_me", ""})


def _ensure_secret_key(settings: dict) -> None:
    settings.setdefault("server", {})
    secret = str(settings["server"].get("secret_key", "")).strip()
    if secret.lower() in _DEFAULT_SECRET_KEYS:
        settings["server"]["secret_key"] = secrets.token_hex(16)


def _resolve_bundled_limiter_template() -> Path | None:
    """Ищет шаблон limiter.toml в установленном simplexng."""
    try:
        import simplexng

        candidate = Path(simplexng.__file__).resolve().parent / "_vendor" / "searx" / "limiter.toml"
        if candidate.is_file():
            return candidate
    except ImportError:
        pass
    return None


def ensure_limiter_toml_file() -> Path:
    """Создаёт limiter.toml рядом с simplexng_settings.yml (ожидание SearXNG)."""
    path = Path(get_default_data_dir()) / "limiter.toml"
    if path.is_file():
        return path

    template = _resolve_bundled_limiter_template()
    if template is not None:
        path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text("[botdetection.ip_limit]\nlink_token = false\n", encoding="utf-8")
    logger.info("Создан limiter.toml: %s", path)
    return path


def _apply_portable_simplexng_overlays(settings: dict) -> dict:
    """Облегчённый профиль SearXNG для portable: меньше движков, без checker/limiter."""
    settings["use_default_settings"] = {
        "engines": {"keep_only": list(_PORTABLE_ENGINE_KEEP)},
    }
    settings.setdefault("server", {})
    settings["server"]["bind_address"] = _DEFAULT_HOST
    settings["server"]["limiter"] = False
    settings.setdefault("checker", {})
    settings["checker"]["off_when_debug"] = True
    settings["checker"]["scheduling"] = {}
    settings.setdefault("search", {})
    formats = settings["search"].setdefault("formats", [])
    if "json" not in formats:
        formats.append("json")
    if sys.platform == "win32":
        settings.setdefault("plugins", {})
        settings["plugins"]["searx.plugins.calculator.SXNGPlugin"] = {"active": False}
    return settings


def ensure_simplexng_settings_file() -> Path:
    """Готовит settings.yml для SimpleXNG в persistent data dir."""
    path = Path(get_default_data_dir()) / "simplexng_settings.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_limiter_toml_file()
    from simplexng.settings import get_bundled_template

    if path.is_file():
        settings = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        settings = yaml.safe_load(get_bundled_template().read_text(encoding="utf-8"))

    settings = _apply_portable_simplexng_overlays(settings)
    _ensure_secret_key(settings)
    path.write_text(yaml.dump(settings, allow_unicode=True), encoding="utf-8")
    return path


def _wait_until_listener(host: str, port: int, *, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _process is not None and _process.poll() is not None:
            logger.error("SearXNG worker завершился с кодом %s", _process.returncode)
            return False
        if _port_has_listener(host, port):
            return True
        time.sleep(_HEALTH_POLL_SEC)
    return False


def _wait_until_healthy(base_url: str, *, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _process is not None and _process.poll() is not None:
            logger.error("SearXNG worker завершился с кодом %s", _process.returncode)
            return False
        if _searxng_healthy(base_url, timeout=1.5):
            return True
        time.sleep(_HEALTH_POLL_SEC)
    return False


def _worker_stderr_log_path() -> Path:
    return Path(get_default_data_dir()) / "logs" / "searxng-worker.log"


def _open_worker_stderr_log():
    global _worker_stderr_file

    log_path = _worker_stderr_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _worker_stderr_file = open(log_path, "ab")
    return _worker_stderr_file


def _read_worker_stderr_tail(max_bytes: int = 4096) -> str:
    log_path = _worker_stderr_log_path()
    if not log_path.is_file():
        return ""
    try:
        data = log_path.read_bytes()
        if not data:
            return ""
        return data.decode("utf-8", errors="replace").strip()[-max_bytes:]
    except OSError:
        return ""


def _worker_command() -> list[str]:
    """Команда для sidecar: frozen exe или launcher.py в dev."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    launcher = Path(get_app_root()) / "launcher.py"
    return [sys.executable, str(launcher)]


def start_bundled_searxng() -> bool:
    """Поднимает SimpleXNG sidecar. Возвращает True при успехе."""
    global _process, _instance_url

    if not bundled_searxng_enabled():
        return False
    if get_bundled_searxng_url():
        return True
    if not is_simplexng_available():
        logger.warning("SimpleXNG не найден — bundled SearXNG пропущен")
        return False

    host = _DEFAULT_HOST
    try:
        port = _pick_port(host)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return False

    settings_path = ensure_simplexng_settings_file()
    url = f"http://{host}:{port}"

    if _searxng_healthy(url, timeout=1.0):
        _instance_url = url
        os.environ[BUNDLED_SEARXNG_URL_ENV] = url
        os.environ["SEARXNG_INSTANCE"] = url
        logger.info("Bundled SearXNG уже отвечает на %s", url)
        return True

    env = os.environ.copy()
    env[SEARXNG_WORKER_ENV] = "1"
    env[_PORT_ENV] = str(port)
    env[_HOST_ENV] = host
    env[_SETTINGS_ENV] = str(settings_path)
    env["SEARXNG_DISABLE_ETC_SETTINGS"] = "1"

    logger.info("Запуск bundled SearXNG на %s", url)
    try:
        _process = subprocess.Popen(
            _worker_command(),
            env=env,
            cwd=get_app_root(),
            creationflags=_creationflags(),
            stderr=_open_worker_stderr_log(),
        )
    except OSError as exc:
        logger.error("Не удалось запустить SearXNG worker: %s", exc)
        _process = None
        return False

    if not _wait_until_listener(host, port, timeout_sec=_LISTENER_TIMEOUT_SEC):
        stderr_tail = _read_worker_stderr_tail()
        if stderr_tail:
            logger.error("SearXNG worker log: %s", stderr_tail)
        logger.error("SearXNG не начал слушать порт %s за %.0f с", port, _LISTENER_TIMEOUT_SEC)
        stop_bundled_searxng()
        return False

    health_deadline = max(15.0, _STARTUP_TIMEOUT_SEC - _LISTENER_TIMEOUT_SEC)
    if not _wait_until_healthy(url, timeout_sec=health_deadline):
        stderr_tail = _read_worker_stderr_tail()
        if stderr_tail:
            logger.error("SearXNG worker log: %s", stderr_tail)
        logger.error("SearXNG не ответил на health-check за %.0f с", health_deadline)
        stop_bundled_searxng()
        return False

    _instance_url = url
    os.environ[BUNDLED_SEARXNG_URL_ENV] = url
    os.environ["SEARXNG_INSTANCE"] = url
    logger.info("Bundled SearXNG готов: %s", url)
    return True


def stop_bundled_searxng() -> None:
    """Останавливает sidecar SearXNG."""
    global _process, _instance_url, _worker_stderr_file

    proc = _process
    _process = None
    _instance_url = ""
    os.environ.pop(BUNDLED_SEARXNG_URL_ENV, None)

    if proc is None:
        return
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    if _worker_stderr_file is not None:
        try:
            _worker_stderr_file.close()
        except Exception:
            pass
        _worker_stderr_file = None
    logger.info("Bundled SearXNG остановлен")
