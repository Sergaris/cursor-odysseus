"""Tests for signed deep-research report access tokens."""

import time
from unittest.mock import patch

from src.research_report_access import sign_report_access, verify_report_access


def test_sign_and_verify_roundtrip():
    token = sign_report_access("rp-abc123", "alice", ttl_sec=600)
    assert verify_report_access("rp-abc123", token) == "alice"


def test_verify_rejects_wrong_session():
    token = sign_report_access("rp-abc123", "alice", ttl_sec=600)
    assert verify_report_access("rp-other", token) is None


def test_verify_rejects_expired_token():
    token = sign_report_access("rp-abc123", "alice", ttl_sec=60)
    with patch("src.research_report_access.time.time", return_value=time.time() + 7200):
        assert verify_report_access("rp-abc123", token) is None


def test_verify_rejects_tampered_token():
    token = sign_report_access("rp-abc123", "alice", ttl_sec=600)
    assert verify_report_access("rp-abc123", token + "x") is None
