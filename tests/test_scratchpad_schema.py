"""Tests for Deep Research scratchpad pydantic schema."""

from src.scratchpad_schema import Scratchpad, validate_scratchpad


def test_valid_scratchpad_parses():
    payload = {
        "sub_topics": [
            {
                "question": "What is X?",
                "status": "partial",
                "key_facts": ["X is Y https://ex.com"],
                "gaps": ["need Z"],
                "conflicts": [],
            }
        ],
        "insights": ["Found X"],
    }
    model = validate_scratchpad(payload)
    assert model is not None
    assert model.sub_topics[0].status == "partial"
    assert model.insights == ["Found X"]


def test_invalid_status_normalized_to_missing():
    model = Scratchpad.model_validate(
        {"sub_topics": [{"question": "Q", "status": "DONE", "key_facts": []}]}
    )
    assert model.sub_topics[0].status == "missing"


def test_validate_scratchpad_returns_none_on_bad_shape():
    assert validate_scratchpad("not a dict") is None
    assert validate_scratchpad({"sub_topics": "oops"}) is None


def test_string_lists_coerced():
    model = Scratchpad.model_validate(
        {
            "sub_topics": [
                {
                    "question": "Q",
                    "status": "covered",
                    "key_facts": "single fact",
                    "gaps": None,
                }
            ],
            "insights": "one insight",
        }
    )
    assert model.sub_topics[0].key_facts == ["single fact"]
    assert model.insights == ["one insight"]
