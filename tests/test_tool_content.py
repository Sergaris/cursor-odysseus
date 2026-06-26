"""Нормализация content для native tools и Cursor SDK JSON args."""

from src.tool_content import (
    build_mcp_args_from_content,
    normalize_tool_content,
    parse_tool_json_payload,
)


def test_parse_tool_json_payload_object():
    assert parse_tool_json_payload('{"code": "print(1)"}') == {"code": "print(1)"}


def test_normalize_python_unwraps_code():
    payload = '{"code": "import matplotlib.pyplot as plt\\nplt.plot([1,2,3])"}'
    assert normalize_tool_content("python", payload).startswith("import matplotlib")


def test_normalize_bash_unwraps_command():
    payload = '{"command": "echo hello"}'
    assert normalize_tool_content("bash", payload) == "echo hello"


def test_normalize_web_fetch_preserves_json_extras():
    payload = '{"url": "https://example.com", "full": true}'
    normalized = normalize_tool_content("web_fetch", payload)
    assert '"full": true' in normalized
    assert "example.com" in normalized


def test_normalize_write_file_multiline():
    payload = '{"path": "/tmp/a.txt", "content": "line1\\nline2"}'
    normalized = normalize_tool_content("write_file", payload)
    assert normalized.startswith("/tmp/a.txt\n")
    assert "line1" in normalized


def test_build_mcp_args_python_from_json():
    payload = '{"code": "x = 1"}'
    assert build_mcp_args_from_content("python", payload) == {"code": "x = 1"}


def test_build_mcp_args_bash_plain_text():
    assert build_mcp_args_from_content("bash", "ls -la") == {"command": "ls -la"}


def test_normalize_plain_text_passthrough():
    code = "print('ok')"
    assert normalize_tool_content("python", code) == code
