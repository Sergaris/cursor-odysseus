"""Pydantic schema for Deep Research scratchpad JSON.

Validation is soft: callers should fall back to the raw LLM string when
validation fails so weak local models do not abort a research run.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

_ALLOWED_STATUS = frozenset({"covered", "partial", "missing"})


class SubTopic(BaseModel):
    """One research sub-question tracked in the scratchpad."""

    model_config = ConfigDict(extra="ignore")

    question: str = ""
    status: str = "missing"
    key_facts: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        text = str(value or "missing").strip().lower()
        return text if text in _ALLOWED_STATUS else "missing"

    @field_validator("key_facts", "gaps", "conflicts", mode="before")
    @classmethod
    def _coerce_str_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []


class Scratchpad(BaseModel):
    """Structured research notes updated each round."""

    model_config = ConfigDict(extra="ignore")

    sub_topics: list[SubTopic] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)

    @field_validator("insights", mode="before")
    @classmethod
    def _coerce_insights(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


def validate_scratchpad(payload: Any) -> Scratchpad | None:
    """Validate scratchpad JSON; return None on failure (soft validation)."""
    try:
        return Scratchpad.model_validate(payload)
    except ValidationError as exc:
        logger.info("Scratchpad schema validation failed: %s", exc)
        return None
