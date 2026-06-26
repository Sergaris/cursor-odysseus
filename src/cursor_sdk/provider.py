"""Идентификация Cursor SDK как LLM-провайдера Odysseus."""



from pathlib import Path

from urllib.parse import parse_qs, urlencode, urlparse



CURSOR_SDK_BASE_URL = "cursor-sdk://local"

CURSOR_SDK_PROVIDER = "cursor-sdk"

# SDK model id «Auto» из ListModels; не подменяем на composer-2.5.

CURSOR_SDK_DEFAULT_AGENT_MODEL = "default"



# Fallback только если cursor-sdk не установлен. Live-список — через model_discovery.

CURSOR_SDK_MODELS = (

    "default",

    "composer-2.5",

    "auto",

)



# SDK routing aliases — pseudo-модели Cursor (default/auto).

CURSOR_SDK_ROUTING_ALIASES = frozenset({"default", "auto"})





def is_cursor_sdk_routing_alias(model: str | None) -> bool:

    """True для pseudo-моделей Cursor SDK (default/auto)."""

    return (model or "").strip() in CURSOR_SDK_ROUTING_ALIASES





def normalize_cursor_sdk_model(model: str | None) -> str:

    """Возвращает model id для SDK без подмены routing aliases и вариантов."""

    cleaned = (model or "").strip()

    if not cleaned:

        return CURSOR_SDK_DEFAULT_AGENT_MODEL

    return cleaned





def filter_cursor_sdk_model_ids(model_ids: list[str]) -> list[str]:

    """Дедупликация и сортировка model id от SDK (без скрытия fast/default)."""

    return sorted({m.strip() for m in model_ids if isinstance(m, str) and m.strip()})





def is_cursor_sdk_base(url: str) -> bool:

    """True, если base_url указывает на локальный Cursor SDK."""

    normalized = (url or "").strip()

    if normalized.startswith(f"{CURSOR_SDK_BASE_URL}?"):

        return True

    return normalized.rstrip("/") in {CURSOR_SDK_BASE_URL, f"{CURSOR_SDK_BASE_URL}/"}





def resolve_cursor_sdk_cwd(base_url: str = "") -> str:

    """Возвращает workspace для local Cursor agent."""

    from src.runtime_paths import get_default_workspace_dir

    from src.settings import get_setting



    parsed = urlparse(base_url or "")

    query = parse_qs(parsed.query)

    for key in ("cwd", "workspace"):

        values = query.get(key) or []

        if values and str(values[0]).strip():

            return str(Path(str(values[0]).strip()).resolve())



    workspace = (get_setting("cursor_workspace", "") or "").strip()

    if workspace:

        return str(Path(workspace).resolve())

    return str(Path(get_default_workspace_dir()).resolve())





def build_cursor_sdk_base_url(workspace: str = "") -> str:

    """Строит base_url endpoint с опциональным workspace в query."""

    workspace = (workspace or "").strip()

    if not workspace:

        return CURSOR_SDK_BASE_URL

    return f"{CURSOR_SDK_BASE_URL}?{urlencode({'cwd': workspace})}"

