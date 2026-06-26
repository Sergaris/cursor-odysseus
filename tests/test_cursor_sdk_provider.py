"""Tests for Cursor SDK provider helpers."""



from src.cursor_sdk.provider import (

    CURSOR_SDK_DEFAULT_AGENT_MODEL,

    is_cursor_sdk_routing_alias,

    normalize_cursor_sdk_model,

    filter_cursor_sdk_model_ids,

)





def test_normalize_cursor_sdk_model_passes_through_sdk_ids():

    assert normalize_cursor_sdk_model("default") == "default"

    assert normalize_cursor_sdk_model("auto") == "auto"

    assert normalize_cursor_sdk_model("") == CURSOR_SDK_DEFAULT_AGENT_MODEL

    assert normalize_cursor_sdk_model("composer-2.5-fast") == "composer-2.5-fast"

    assert normalize_cursor_sdk_model("composer-2.5") == "composer-2.5"

    assert normalize_cursor_sdk_model("gpt-5.1-codex-max") == "gpt-5.1-codex-max"





def test_filter_cursor_sdk_model_ids_dedupes_only():

    assert filter_cursor_sdk_model_ids(

        ["composer-2.5-fast", "composer-2.5", "gpt-5.5", "composer-2.5"]

    ) == ["composer-2.5", "composer-2.5-fast", "gpt-5.5"]





def test_is_cursor_sdk_routing_alias():

    assert is_cursor_sdk_routing_alias("default") is True

    assert is_cursor_sdk_routing_alias("composer-2.5") is False

