"""
tests/test_api.py — API endpoint tests for MedBook.

Tests cover:
  - GET /health
  - POST /chat (stateless REST)
  - WebSocket /ws/chat (stateful session)

Running:
    # Install test deps first (if not already):
    #   pip install pytest pytest-asyncio httpx
    #
    pytest tests/test_api.py -v

Notes:
  - These are INTEGRATION tests — they require a running Neo4j instance and
    a valid OPENAI_API_KEY in your .env file.
  - The tests mock the LLM and DB calls so they run fast without hitting
    OpenAI or Neo4j. See the "unit" section for pure unit tests.
  - Run with:  pytest tests/test_api.py -v -k "not integration"
    to skip tests that need live services.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Patch heavy dependencies BEFORE importing the app so lifespan doesn't run
# ---------------------------------------------------------------------------

# We patch at module level so that when api.py is imported it sees mocks.
_mock_db = AsyncMock()
_mock_memory = AsyncMock()
_mock_llm = MagicMock()


def _make_text_response(content: str) -> MagicMock:
    """Build a fake OpenAI ChatCompletion response with no tool calls."""
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.fixture(autouse=True)
def patch_globals(monkeypatch):
    """
    Replace the global db / memory / llm in api.py with mocks so tests
    never touch Neo4j or OpenAI.
    """
    import app.api as api_module

    monkeypatch.setattr(api_module, "db", _mock_db)
    monkeypatch.setattr(api_module, "memory", _mock_memory)
    monkeypatch.setattr(api_module, "llm", _mock_llm)


@pytest.fixture()
def client():
    """
    Synchronous TestClient — wraps the ASGI app, bypasses lifespan so we
    don't need a real Neo4j on startup.
    """
    from app.api import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ===========================================================================
# Helper: configure the mocked LLM to return a canned text reply
# ===========================================================================

def mock_agent_reply(reply: str):
    """
    Patch agent_loop to return *reply* immediately (no real LLM call).

    Also mutates the messages list exactly like the real agent_loop does
    (appends user + assistant messages), so tests that inspect the returned
    messages array see the expected content.
    """
    async def _side_effect(llm, db, memory, messages, user_message):
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": reply})
        return reply

    # AsyncMock with side_effect still records call_args, so tests that
    # introspect call arguments (e.g. test_passing_prior_messages_preserves_context)
    # continue to work.
    mock = AsyncMock(side_effect=_side_effect)
    return patch("app.api.agent_loop", new=mock)


# ===========================================================================
# 1. GET /health
# ===========================================================================


class TestHealth:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_response_has_neo4j_field(self, client):
        response = client.get("/health")
        assert "neo4j" in response.json()


# ===========================================================================
# 2. POST /chat — stateless REST
# ===========================================================================


class TestPostChat:
    def test_fresh_session_no_messages(self, client):
        """No messages payload → server creates system prompt automatically."""
        with mock_agent_reply("Hello! What is your phone number?"):
            response = client.post(
                "/chat",
                json={"user_message": "I have a headache"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert data["reply"] == "Hello! What is your phone number?"

    def test_reply_includes_messages_array(self, client):
        """Response must echo back the updated messages list."""
        with mock_agent_reply("Please share your phone number."):
            response = client.post(
                "/chat",
                json={"user_message": "I want to book an appointment"},
            )
        data = response.json()
        assert "messages" in data
        assert isinstance(data["messages"], list)
        # Must contain at least system + user + assistant
        assert len(data["messages"]) >= 3

    def test_messages_roles_are_valid(self, client):
        """Every message in the returned array must have a valid role."""
        valid_roles = {"system", "user", "assistant", "tool"}
        with mock_agent_reply("Sure, what is your phone number?"):
            response = client.post(
                "/chat",
                json={"user_message": "Book me a doctor"},
            )
        for msg in response.json()["messages"]:
            assert msg["role"] in valid_roles

    def test_passing_prior_messages_preserves_context(self, client):
        """Client-supplied messages are forwarded to agent_loop."""
        prior_messages: list[dict[str, Any]] = [
            {"role": "system", "content": "You are MedBook."},
            {"role": "user", "content": "I have a headache"},
            {"role": "assistant", "content": "What is your phone number?"},
        ]
        with mock_agent_reply("Thanks! Registering you now.") as mock_loop:
            response = client.post(
                "/chat",
                json={
                    "user_message": "9876543210",
                    "messages": prior_messages,
                },
            )
        assert response.status_code == 200
        # Verify agent_loop was called with the supplied messages
        call_args = mock_loop.call_args
        passed_messages = call_args.args[3]  # positional: llm, db, memory, messages, user_msg
        assert passed_messages[0]["role"] == "system"
        assert passed_messages[1]["role"] == "user"

    def test_user_message_appended_to_messages(self, client):
        """The user's message must appear in the returned messages list."""
        user_msg = "I want to see Dr. Sharma"
        with mock_agent_reply("Dr. Sharma is available!"):
            response = client.post(
                "/chat",
                json={"user_message": user_msg},
            )
        messages = response.json()["messages"]
        user_messages = [m for m in messages if m["role"] == "user"]
        assert any(user_msg in m["content"] for m in user_messages)

    def test_empty_message_still_responds(self, client):
        """An empty string user_message is valid (agent decides what to do)."""
        with mock_agent_reply("I didn't catch that. Could you repeat?"):
            response = client.post(
                "/chat",
                json={"user_message": ""},
            )
        assert response.status_code == 200
        assert "reply" in response.json()

    def test_agent_exception_returns_error(self, client):
        """If agent_loop raises, the endpoint returns an error dict (no 500)."""
        with patch("app.api.agent_loop", new=AsyncMock(side_effect=RuntimeError("boom"))):
            response = client.post(
                "/chat",
                json={"user_message": "crash please"},
            )
        # Should not be a 500 — the endpoint catches exceptions
        assert response.status_code == 200
        assert "error" in response.json()

    def test_multi_turn_conversation_state_grows(self, client):
        """
        Simulate two turns by passing the messages from turn 1 into turn 2.
        The returned messages list should be larger after turn 2.
        """
        with mock_agent_reply("What is your phone number?"):
            r1 = client.post("/chat", json={"user_message": "I have a headache"})
        messages_after_turn1 = r1.json()["messages"]

        with mock_agent_reply("Please share your name."):
            r2 = client.post(
                "/chat",
                json={
                    "user_message": "9045873890",
                    "messages": messages_after_turn1,
                },
            )
        messages_after_turn2 = r2.json()["messages"]
        assert len(messages_after_turn2) > len(messages_after_turn1)

    def test_missing_user_message_field_returns_422(self, client):
        """Pydantic validation — user_message is required."""
        response = client.post("/chat", json={})
        assert response.status_code == 422


# ===========================================================================
# 3. WebSocket /ws/chat — stateful session
# ===========================================================================


class TestWebSocketChat:
    def test_connect_receives_session_id(self, client):
        """On connect, server sends a 'connected' message with a session_id."""
        with client.websocket_connect("/ws/chat") as ws:
            greeting = ws.receive_json()
            assert greeting["type"] == "connected"
            assert "session_id" in greeting
            assert len(greeting["session_id"]) > 0

    def test_connect_message_type_is_connected(self, client):
        with client.websocket_connect("/ws/chat") as ws:
            greeting = ws.receive_json()
            assert greeting["type"] == "connected"

    def test_send_message_receives_reply(self, client):
        """Sending a text message should receive a reply with type 'reply'."""
        with mock_agent_reply("What is your phone number?"):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_json()  # consume greeting
                ws.send_text("I have a headache")
                reply = ws.receive_json()
        assert reply["type"] == "reply"
        assert "message" in reply
        assert reply["message"] == "What is your phone number?"

    def test_reset_clears_session(self, client):
        """Sending 'reset' should return a system message and clear context."""
        with mock_agent_reply("Some reply"):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_json()  # greeting
                ws.send_text("I have a headache")
                ws.receive_json()  # reply

                ws.send_text("reset")
                reset_msg = ws.receive_json()

        assert reset_msg["type"] == "system"
        assert "reset" in reset_msg["message"].lower()

    def test_reset_case_insensitive(self, client):
        """'RESET', 'Reset', 'reset' should all trigger a session reset."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.receive_json()  # greeting
            ws.send_text("RESET")
            reset_msg = ws.receive_json()
        assert reset_msg["type"] == "system"

    def test_multi_turn_within_same_connection(self, client):
        """Server should maintain context across turns in the same WS session."""
        replies = [
            "What is your phone number?",
            "What is your name?",
            "What is your age?",
        ]
        call_count = 0

        async def fake_agent_loop(llm, db, memory, messages, user_msg):
            nonlocal call_count
            response = replies[call_count]
            call_count += 1
            return response

        with patch("app.api.agent_loop", new=fake_agent_loop):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_json()  # greeting

                ws.send_text("I have a headache")
                r1 = ws.receive_json()

                ws.send_text("9045873890")
                r2 = ws.receive_json()

                ws.send_text("Himanshu")
                r3 = ws.receive_json()

        assert r1["message"] == "What is your phone number?"
        assert r2["message"] == "What is your name?"
        assert r3["message"] == "What is your age?"
        assert call_count == 3

    def test_agent_exception_returns_error_type(self, client):
        """If agent_loop raises inside WS, server sends error type (no crash)."""
        with patch("app.api.agent_loop", new=AsyncMock(side_effect=RuntimeError("oops"))):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_json()  # greeting
                ws.send_text("crash me")
                error_msg = ws.receive_json()
        assert error_msg["type"] == "error"
        assert "message" in error_msg

    def test_each_connection_gets_unique_session_id(self, client):
        """Two separate WS connections must have different session IDs."""
        with client.websocket_connect("/ws/chat") as ws1:
            g1 = ws1.receive_json()

        with client.websocket_connect("/ws/chat") as ws2:
            g2 = ws2.receive_json()

        assert g1["session_id"] != g2["session_id"]

    def test_reset_then_continue_works(self, client):
        """After reset, subsequent messages should still get a valid reply."""
        call_count = 0

        async def fake_loop(llm, db, memory, messages, user_msg):
            nonlocal call_count
            call_count += 1
            return f"Reply #{call_count}"

        with patch("app.api.agent_loop", new=fake_loop):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_json()  # greeting

                ws.send_text("first message")
                ws.receive_json()

                ws.send_text("reset")
                ws.receive_json()  # system reset message

                ws.send_text("second message after reset")
                reply = ws.receive_json()

        assert reply["type"] == "reply"
        assert "Reply" in reply["message"]


# ===========================================================================
# 4. Docs endpoint (sanity check)
# ===========================================================================


class TestDocs:
    def test_swagger_ui_is_accessible(self, client):
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_openapi_schema_is_accessible(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "paths" in schema
        assert "/health" in schema["paths"]
        assert "/chat" in schema["paths"]
