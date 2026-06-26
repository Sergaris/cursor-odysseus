"""Тесты messages_to_prompt."""

from src.cursor_sdk.prompt import TEXT_ONLY_SUFFIX, messages_to_prompt


def test_messages_to_prompt_chat_default_no_suffix():
    prompt = messages_to_prompt([
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ])
    assert "User:\nHello" in prompt
    assert "Assistant:\nHi there" in prompt
    assert TEXT_ONLY_SUFFIX.strip() not in prompt


def test_messages_to_prompt_research_text_only():
    prompt = messages_to_prompt(
        [{"role": "user", "content": "Plan research"}],
        text_only=True,
    )
    assert prompt.endswith(TEXT_ONLY_SUFFIX.strip())


def test_messages_to_prompt_skips_empty():
    prompt = messages_to_prompt([{"role": "user", "content": "  "}])
    assert "User:\n(empty message)" in prompt
