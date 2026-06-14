"""
api.py — FastAPI endpoints for MedBook.

Exposes the agentic booking loop over:
  1. POST /chat (stateless REST)
  2. WebSocket /ws/chat (stateful sessions)

Usage:
  uvicorn app.api:app --reload
"""

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.assistant import SYSTEM_PROMPT, agent_loop
from app.db import HospitalDB
from app.llm import LLM
from app.logger import get_logger
from app.memory import GraphMemory, quiet_graphiti_logs

load_dotenv()
quiet_graphiti_logs()
log = get_logger(__name__)

# Global instances initialized during lifespan
db: HospitalDB = None  # type: ignore
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

    db = HospitalDB(uri, user, password)
    memory = GraphMemory(uri, user, password)
    llm = LLM()

    log.info("Starting up API, connecting to Neo4j...")
    await db.connect()
    await memory.setup()
    log.info("Connected to Neo4j and Graphiti.")

    yield

    log.info("Shutting down API, closing connections...")
    await db.close()
    await memory.close()


app = FastAPI(
    title="MedBook API",
    description="Agentic Doctor Appointment Booking System",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_initial_messages() -> list[dict[str, Any]]:
    tz_name = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d (%A)")
    current_time = now.strftime("%H:%M")
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                today=today, current_time=current_time, timezone=tz_name
            ),
        }
    ]


class ChatRequest(BaseModel):
    user_message: str
    messages: list[dict[str, Any]] | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "neo4j": "connected"}


@app.post("/chat")
async def chat(req: ChatRequest):
    """Stateless REST endpoint. Send previous messages if you want context."""
    messages = req.messages if req.messages else _get_initial_messages()
    
    try:
        reply = await agent_loop(llm, db, memory, messages, req.user_message)
        return {"reply": reply, "messages": messages}
    except Exception as exc:
        log.exception("Error in POST /chat")
        return {"error": "Internal server error"}


# ------------------------------------------------------------------
# WebSocket Stateful Chat
# ------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        # Maps session_id -> list of messages
        self.sessions: dict[str, list[dict[str, Any]]] = {}

    def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = _get_initial_messages()
        return session_id

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        if session_id not in self.sessions:
            self.sessions[session_id] = _get_initial_messages()
        return self.sessions[session_id]

    def reset_session(self, session_id: str):
        self.sessions[session_id] = _get_initial_messages()


manager = ConnectionManager()


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    session_id = manager.create_session()
    
    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "message": "Connected to MedBook API."
    })
    
    log.info("WebSocket connected: %s", session_id)

    try:
        while True:
            data = await websocket.receive_text()
            
            if data.lower().strip() == "reset":
                manager.reset_session(session_id)
                await websocket.send_json({
                    "type": "system",
                    "message": "--- Conversation reset. Start fresh! ---"
                })
                continue
                
            messages = manager.get_messages(session_id)
            
            try:
                reply = await agent_loop(llm, db, memory, messages, data)
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
        # We could delete the session here, but keeping it allows reconnects
        # if the client somehow knew its session_id (not implemented yet).
