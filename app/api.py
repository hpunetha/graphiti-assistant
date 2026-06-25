"""
api.py — FastAPI endpoints for MedBook.

Exposes the agentic booking loop over:
  1. POST /chat (stateless REST)
  2. WebSocket /ws/chat (stateful sessions)
  3. GET /ws-test  (browser WebSocket test UI)

Usage:
  uvicorn app.api:app --reload
"""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.assistant import agent_loop, build_system_prompt
from app.api_client import HospitalApiClient
from app.formatting import TTS, UI, normalize_mode, to_tts
from app.llm import LLM
from app.logger import get_logger
from app.memory import GraphMemory, quiet_graphiti_logs

load_dotenv()
quiet_graphiti_logs()
log = get_logger(__name__)

# Global instances initialized during lifespan
db: HospitalApiClient = None  # type: ignore
memory: GraphMemory = None  # type: ignore
llm: LLM = None  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, memory, llm
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    if not os.environ.get("OPENAI_API_KEY"):
        log.error("OPENAI_API_KEY not set!")

    hospital_api_url = os.environ.get("HOSPITAL_API_URL", "http://localhost:8001")
    db = HospitalApiClient(hospital_api_url)
    memory = GraphMemory(uri, user, password)
    llm = LLM()

    log.info("Starting up API, connecting to API and Neo4j...")
    await db.connect()
    await memory.setup()
    log.info("Connected to Hospital API and Graphiti.")

    yield

    log.info("Shutting down API, closing connections...")
    await db.close()
    await memory.close()


app = FastAPI(
    title="MedBook API",
    description="Agentic Doctor Appointment Booking System",
    lifespan=lifespan,
)

# Serve static assets (ws_test.html, etc.) from app/static/
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_initial_messages(mode: str = UI) -> list[dict[str, Any]]:
    tz_name = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d (%A)")
    current_time = now.strftime("%H:%M")
    return [
        {
            "role": "system",
            "content": build_system_prompt(today, current_time, tz_name, mode),
        }
    ]


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatRequest(BaseModel):
    user_message: str
    messages: list[ChatMessage] | None = None
    response_mode: str = UI  # "ui" (rich) or "tts" (spoken-friendly)


@app.get("/health")
async def health():
    return {"status": "ok", "neo4j": "connected"}


@app.get("/ws-test", response_class=HTMLResponse, include_in_schema=False)
async def ws_test_ui():
    """Browser-based WebSocket chat tester (not shown in OpenAPI docs)."""
    html_path = os.path.join(os.path.dirname(__file__), "static", "ws_test.html")
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/chat")
async def chat(req: ChatRequest):
    """Stateless REST endpoint. Send previous messages if you want context.

    Set ``response_mode`` to ``"tts"`` for spoken-friendly replies (no markdown
    or emoji, numbers/times/prices spelled out as words). When you supply your
    own ``messages``, the system prompt you send governs the model's style; the
    sanitizer still guarantees clean output for ``tts``.
    """
    mode = normalize_mode(req.response_mode)
    messages = (
        [m.model_dump(exclude_none=True) for m in req.messages]
        if req.messages
        else _get_initial_messages(mode)
    )

    try:
        reply = await agent_loop(llm, db, memory, messages, req.user_message)
        # Background: auto-ingest this turn into Graphiti once the patient is known.
        phone = _extract_patient_phone(messages)
        if phone:
            _schedule_auto_ingest(memory, req.user_message, reply, phone)
        if mode == TTS:
            reply = to_tts(reply)
        return {"reply": reply, "messages": messages}
    except Exception as exc:
        log.exception("Error in POST /chat")
        return {"error": "Internal server error"}


# ------------------------------------------------------------------
# WebSocket Stateful Chat
# ------------------------------------------------------------------


def _schedule_auto_ingest(
    mem: "GraphMemory", user_msg: str, reply: str, phone: str
) -> None:
    """Wrap asyncio.create_task for auto_ingest so tests can mock it cleanly."""
    asyncio.create_task(mem.auto_ingest(user_msg, reply, phone))


def _extract_patient_phone(messages: list[dict]) -> str | None:
    """Scan recent tool results for an identify_patient response that has a phone.

    The identify_patient tool returns {"status": ..., "patient": {"phone": ..., ...}}.
    We scan the last 10 messages in reverse so we find the most recent call first.
    """
    for msg in reversed(messages[-10:]):
        if msg.get("role") == "tool":
            try:
                data = json.loads(msg["content"])
                phone = (data.get("patient") or {}).get("phone")
                if phone:
                    return str(phone)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
    return None


class Session:
    """Per-connection chat state: conversation history, output mode, and patient phone."""

    def __init__(self, mode: str = UI):
        self.mode = normalize_mode(mode)
        self.messages = _get_initial_messages(self.mode)
        self.patient_phone: str | None = None


class ConnectionManager:
    def __init__(self):
        # Maps session_id -> Session
        self.sessions: dict[str, Session] = {}

    def create_session(self, mode: str = UI) -> str:
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = Session(mode)
        return session_id

    def get_session(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            self.sessions[session_id] = Session()
        return self.sessions[session_id]

    def reset_session(self, session_id: str):
        # Preserve the session's output mode across a reset.
        mode = self.sessions[session_id].mode if session_id in self.sessions else UI
        self.sessions[session_id] = Session(mode)

    def set_mode(self, session_id: str, mode: str):
        """Switch output mode mid-conversation, rebuilding the system prompt."""
        session = self.get_session(session_id)
        session.mode = normalize_mode(mode)
        session.messages[0] = _get_initial_messages(session.mode)[0]

    def remove_session(self, session_id: str):
        self.sessions.pop(session_id, None)


manager = ConnectionManager()


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    # Output mode is chosen at connect time via ?mode=tts (defaults to ui).
    mode = normalize_mode(websocket.query_params.get("mode"))
    session_id = manager.create_session(mode)

    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "mode": mode,
        "message": "Connected to MedBook API."
    })

    log.info("WebSocket connected: %s (mode: %s)", session_id, mode)

    try:
        while True:
            data = await websocket.receive_text()
            command = data.lower().strip()

            if command == "reset":
                manager.reset_session(session_id)
                await websocket.send_json({
                    "type": "system",
                    "message": "--- Conversation reset. Start fresh! ---"
                })
                continue

            # Runtime mode switch: "mode: tts" / "mode: ui"
            if command.startswith("mode:"):
                requested = command.split(":", 1)[1].strip()
                manager.set_mode(session_id, requested)
                new_mode = manager.get_session(session_id).mode
                await websocket.send_json({
                    "type": "system",
                    "mode": new_mode,
                    "message": f"--- Output mode set to {new_mode}. ---"
                })
                continue

            session = manager.get_session(session_id)

            try:
                reply = await agent_loop(llm, db, memory, session.messages, data)
                # Cache phone once identified; fire background memory ingest.
                if not session.patient_phone:
                    session.patient_phone = _extract_patient_phone(session.messages)
                if session.patient_phone:
                    _schedule_auto_ingest(memory, data, reply, session.patient_phone)
                if session.mode == TTS:
                    reply = to_tts(reply)
                await websocket.send_json({
                    "type": "reply",
                    "message": reply
                })
            except Exception as exc:
                log.exception("Error in WS /chat for session %s", session_id)
                await websocket.send_json({
                    "type": "error",
                    "message": "Sorry, something went wrong processing your request."
                })

    except WebSocketDisconnect:
        log.info("WebSocket disconnected: %s", session_id)
        manager.remove_session(session_id)
