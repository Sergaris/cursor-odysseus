"""Тест release Cursor SDK chat agent при удалении сессии."""

from unittest.mock import MagicMock, patch

from core.session_manager import SessionManager


def test_delete_session_releases_cursor_chat_agent():
    manager = SessionManager()
    manager.sessions["ghost-session"] = MagicMock()

    with patch("src.cursor_sdk.stream_bridge.release_chat_session") as release_mock:
        with patch.object(manager, "sessions", {"ghost-session": MagicMock()}):
            with patch("core.session_manager.SessionLocal") as session_local:
                db = MagicMock()
                session_local.return_value = db
                db.query.return_value.filter.return_value.update.return_value = None
                db.query.return_value.filter.return_value.delete.return_value = None
                db.query.return_value.filter.return_value.first.return_value = None

                ok = manager.delete_session("ghost-session")

    assert ok is True
    release_mock.assert_called_once_with("ghost-session")
