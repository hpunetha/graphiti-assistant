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
                    "user_message": "8952039590",
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

                ws.send_text("8952039590")
                r2 = ws.receive_json()

                ws.send_text("Harsh")
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


# ===========================================================================
# 5. app.formatting — to_tts sanitizer (pure unit tests, no network needed)
# ===========================================================================


class TestToTts:
    """Verify that to_tts produces spoken-friendly text without raw digits or
    markdown symbols, and that plain prose is not mangled."""

    from app.formatting import to_tts as _to_tts  # class-level import for speed

    def _clean(self, text: str) -> str:
        from app.formatting import to_tts
        return to_tts(text)

    # ── Markdown stripping ─────────────────────────────────────────────────

    def test_strips_bold(self):
        assert "**" not in self._clean("**Confirmed!** See Dr. Smith.")

    def test_strips_headers(self):
        assert "#" not in self._clean("# Appointment Details\nSee Dr. Smith.")

    def test_strips_emoji(self):
        result = self._clean("Welcome to City Care Hospital! 🏥")
        assert "🏥" not in result
        assert "Hospital" in result

    def test_converts_markdown_link(self):
        result = self._clean("Visit [our portal](https://example.com) for details.")
        assert "[" not in result
        assert "our portal" in result
        assert "https" not in result

    def test_flattens_bullet_list(self):
        text = "Available slots:\n- 9:00 AM\n- 5:05 PM\n- 6:40 PM"
        result = self._clean(text)
        assert "- " not in result
        # All slot items still present as text
        assert "nine" in result
        assert "five" in result

    def test_plain_prose_unchanged(self):
        prose = "Please tell me your name."
        assert self._clean(prose) == prose

    # ── Number to words ────────────────────────────────────────────────────

    def test_time_hhmm_am(self):
        result = self._clean("9:00 AM")
        assert "9" not in result
        assert "nine" in result.lower()

    def test_time_hhmm_pm_with_minutes(self):
        result = self._clean("6:40 PM")
        assert "6" not in result
        assert "six" in result.lower()
        assert "forty" in result.lower()

    def test_time_odd_minutes(self):
        result = self._clean("9:05 AM")
        assert "9" not in result
        assert "five" in result.lower()

    def test_currency_rupees(self):
        result = self._clean("The fee is ₹500.")
        assert "500" not in result
        assert "five hundred" in result.lower()
        assert "rupees" in result.lower()

    def test_currency_rupees_decimal(self):
        result = self._clean("Total: ₹20.50")
        assert "20" not in result
        assert "rupees" in result.lower()
        assert "paise" in result.lower()

    def test_currency_dollars(self):
        result = self._clean("Cost: $20")
        assert "20" not in result
        assert "twenty" in result.lower()
        assert "dollars" in result.lower()

    def test_date_iso(self):
        result = self._clean("Appointment on 2026-06-20.")
        assert "2026" not in result
        assert "june" in result.lower()

    def test_phone_digit_by_digit(self):
        result = self._clean("Call 9876543210.")
        # No long digit runs — must be spelled digit by digit
        assert "9876543210" not in result
        assert "nine" in result.lower()

    def test_ordinal(self):
        result = self._clean("You are 1st in queue.")
        assert "1st" not in result
        assert "first" in result.lower()

    def test_percent(self):
        result = self._clean("50% off today.")
        assert "50%" not in result
        assert "fifty percent" in result.lower()

    def test_decimal(self):
        result = self._clean("Rating: 3.5 stars.")
        assert "3.5" not in result
        assert "three" in result.lower()

    def test_plain_integer(self):
        result = self._clean("We have 2 slots left.")
        assert "2" not in result
        assert "two" in result.lower()

    def test_no_raw_digits_in_combined_reply(self):
        """A realistic booking-confirmation reply must contain no raw digits."""
        text = (
            "**Confirmed!** Your appointment with Dr. Smith is at 6:40 PM "
            "on 2026-06-20. The fee is ₹500. Your booking ID is 50023. 🏥"
        )
        result = self._clean(text)
        import re
        assert not re.search(r"\d", result), f"raw digit found: {result!r}"


# ===========================================================================
# 6. build_system_prompt — correct style block per mode
# ===========================================================================


class TestBuildSystemPrompt:
    def test_ui_mode_contains_markdown_guidance(self):
        from app.assistant import build_system_prompt
        prompt = build_system_prompt("2026-06-20 (Saturday)", "10:00", "Asia/Kolkata", "ui")
        assert "markdown" in prompt.lower() or "emoji" in prompt.lower()

    def test_tts_mode_contains_spoken_guidance(self):
        from app.assistant import build_system_prompt
        prompt = build_system_prompt("2026-06-20 (Saturday)", "10:00", "Asia/Kolkata", "tts")
        assert "tts" in prompt.lower() or "spoken" in prompt.lower() or "voice" in prompt.lower()

    def test_tts_mode_forbids_markdown(self):
        from app.assistant import build_system_prompt
        prompt = build_system_prompt("2026-06-20 (Saturday)", "10:00", "Asia/Kolkata", "tts")
        assert "no markdown" in prompt.lower() or "NO markdown" in prompt

    def test_unknown_mode_falls_back_to_ui(self):
        from app.assistant import build_system_prompt
        prompt = build_system_prompt("2026-06-20 (Saturday)", "10:00", "Asia/Kolkata", "invalid")
        # Should not crash; should be identical to ui mode
        ui_prompt = build_system_prompt("2026-06-20 (Saturday)", "10:00", "Asia/Kolkata", "ui")
        assert prompt == ui_prompt


# ===========================================================================
# 7. REST /chat — response_mode field
# ===========================================================================


class TestPostChatTtsMode:
    def test_tts_mode_sanitizes_markdown_in_reply(self, client):
        """When response_mode=tts, markdown in the agent reply is stripped."""
        with mock_agent_reply("**Confirmed!** See Dr. Smith at 6:40 PM. 🏥"):
            response = client.post(
                "/chat",
                json={"user_message": "book it", "response_mode": "tts"},
            )
        reply = response.json()["reply"]
        assert "**" not in reply
        assert "🏥" not in reply

    def test_tts_mode_converts_digits_to_words(self, client):
        """When response_mode=tts, raw digits are spelled as words."""
        with mock_agent_reply("Your slot is at 9:00 AM and costs ₹500."):
            response = client.post(
                "/chat",
                json={"user_message": "any slot", "response_mode": "tts"},
            )
        reply = response.json()["reply"]
        import re
        assert not re.search(r"\d", reply), f"raw digit found: {reply!r}"

    def test_ui_mode_preserves_markdown(self, client):
        """Default (ui) mode must NOT sanitize the reply."""
        raw = "**Confirmed!** 🏥 Slot at 9:00 AM."
        with mock_agent_reply(raw):
            response = client.post(
                "/chat",
                json={"user_message": "book it"},
            )
        assert response.json()["reply"] == raw

    def test_invalid_mode_defaults_to_ui(self, client):
        """An unrecognised response_mode must not raise an error."""
        raw = "**Bold reply**"
        with mock_agent_reply(raw):
            response = client.post(
                "/chat",
                json={"user_message": "hello", "response_mode": "banana"},
            )
        assert response.status_code == 200
        assert response.json()["reply"] == raw


# ===========================================================================
# 8. WebSocket — mode query param + runtime mode switch
# ===========================================================================


class TestWebSocketMode:
    def test_connect_tts_mode_included_in_greeting(self, client):
        with client.websocket_connect("/ws/chat?mode=tts") as ws:
            greeting = ws.receive_json()
        assert greeting.get("mode") == "tts"

    def test_connect_default_mode_is_ui(self, client):
        with client.websocket_connect("/ws/chat") as ws:
            greeting = ws.receive_json()
        assert greeting.get("mode") == "ui"

    def test_tts_mode_sanitizes_ws_reply(self, client):
        with mock_agent_reply("**Hello!** Your slot is at 6:40 PM. 🏥"):
            with client.websocket_connect("/ws/chat?mode=tts") as ws:
                ws.receive_json()  # greeting
                ws.send_text("hi")
                reply = ws.receive_json()
        assert "**" not in reply["message"]
        assert "🏥" not in reply["message"]

    def test_tts_mode_ws_digits_spelled(self, client):
        with mock_agent_reply("Fee: ₹500 at 9:00 AM."):
            with client.websocket_connect("/ws/chat?mode=tts") as ws:
                ws.receive_json()
                ws.send_text("any slot")
                reply = ws.receive_json()
        import re
        assert not re.search(r"\d", reply["message"]), f"digit found: {reply['message']!r}"

    def test_runtime_mode_switch_command(self, client):
        """Sending 'mode: tts' mid-conversation switches mode and acks."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.receive_json()  # greeting (ui mode)
            ws.send_text("mode: tts")
            ack = ws.receive_json()
        assert ack["type"] == "system"
        assert ack.get("mode") == "tts"

    def test_ui_mode_preserves_markdown_in_ws(self, client):
        raw = "**Bold!** 🏥 Slot at 9:00 AM."
        with mock_agent_reply(raw):
            with client.websocket_connect("/ws/chat") as ws:  # default ui
                ws.receive_json()
                ws.send_text("hi")
                reply = ws.receive_json()
        assert reply["message"] == raw
