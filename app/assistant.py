"""
assistant.py — Agentic doctor appointment booking assistant.

An interactive chat loop that uses OpenAI function calling to drive a
tool-based booking workflow. The agent can search for doctors, check
slot availability, book appointments, and more — all by calling tools
that query Neo4j in real-time.

The agent adapts to:
  - Patient demographics (age → pediatrics, gender → gynecology)
  - Symptom descriptions (via Graphiti semantic search)
  - Real-time slot changes (the slot_modifier script can change availability)
  - Multi-turn conversations (full history maintained)

Usage:
    1. docker compose up -d           # start Neo4j
    2. python -m app.seed_hospital    # seed the data (first time)
    3. python -m app.assistant        # chat! (type 'quit' to exit)
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.db import HospitalDB
from app.llm import LLM
from app.logger import get_logger
from app.memory import GraphMemory, quiet_graphiti_logs
from app.tools import TOOL_SCHEMAS, execute_tool

load_dotenv()
quiet_graphiti_logs()

log = get_logger(__name__)

SYSTEM_PROMPT = """\
You are MedBook, a friendly and professional hospital appointment booking assistant.
Your job is to help patients find the right doctor and book appointments.

Today's date is {today}. Current time is {current_time} ({timezone}).

## Your Workflow
1. **Identify the patient** — Always start by asking for their phone number.
   Use the identify_patient tool to look them up. If they're new, collect their
   name, age, and gender — you may gather these ONE AT A TIME across separate
   conversational turns. Only call identify_patient to register once you have
   all three pieces of information.

2. **Understand their need** — They might:
   - Name a specific doctor → use search_doctors with the name
   - Mention a speciality → use search_doctors with the speciality
   - Describe symptoms → use suggest_speciality to find the right specialist
   - Ask to see their bookings → use get_my_bookings
   - Want to cancel → use cancel_booking

3. **Find available slots** — Once a doctor is identified, ask what date works
   for them. Then ask (or infer from their message) what time of day they prefer:
   - **Morning** — before 12:00 PM
   - **Afternoon** — 12:00 PM to 5:00 PM
   - **Evening** — 5:00 PM to 7:00 PM
   - Or an exact time if they specify one.
   Pass this as the `time_of_day` parameter to get_available_slots.
   Slots that have already passed today are automatically excluded.

4. **Book the appointment** — When they choose a slot, use book_appointment.
   If the slot is no longer available (someone else booked it or the clinic
   blocked it), apologize and show updated availability.

## Important Rules
- Be warm and conversational, but concise.
- Always confirm the booking details before finalizing.
- If a child (age < 14) has health concerns, recommend pediatric specialists.
- If a patient mentions pregnancy or women's health, recommend Gynecology
  or Fetal Medicine specialists.
- Never invent information about doctors or slots — only use what the tools return.
- When showing slots, display them in a clear, readable format.
- When a booking is confirmed, show all the details: doctor, date, time, booking ID.
- You can handle multiple bookings in one conversation.
"""

MAX_TOOL_ITERATIONS = 10  # Safety limit for the agent loop


async def agent_loop(
    llm: LLM,
    db: HospitalDB,
    memory: GraphMemory,
    messages: list[dict],
    user_message: str,
) -> str:
    """Run the agentic tool-calling loop for a single user turn.

    Appends the user message, calls the LLM, executes any tool calls,
    and loops until the LLM produces a text response (no more tool calls).

    Returns the final assistant text response.
    """
    log.debug("User message: %s", user_message)
    messages.append({"role": "user", "content": user_message})

    for iteration in range(MAX_TOOL_ITERATIONS):
        log.debug("Agent loop iteration %d", iteration + 1)
        response = llm.chat_with_tools(messages, tools=TOOL_SCHEMAS)
        choice = response.choices[0]
        assistant_msg = choice.message

        # Build the message dict to append to history
        msg_dict: dict = {"role": "assistant", "content": assistant_msg.content or ""}
        if assistant_msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in assistant_msg.tool_calls
            ]
        messages.append(msg_dict)

        # If no tool calls, we have the final response
        if not assistant_msg.tool_calls:
            log.debug("Final bot response: %s", (assistant_msg.content or "")[:200])
            return assistant_msg.content or ""

        # Execute each tool call and append results
        for tool_call in assistant_msg.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            log.info("Tool call: %s(%s)", fn_name, json.dumps(fn_args, ensure_ascii=False)[:120])
            print(f"      [tool] {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:80]})")

            try:
                result = await execute_tool(fn_name, fn_args, db, memory)
                log.debug("Tool result [%s]: %s", fn_name, result[:300])
            except Exception as exc:
                log.exception("Tool %s raised an exception: %s", fn_name, exc)
                result = json.dumps({"error": str(exc)})

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

        # If it's the last iteration, tell the model to respond in text
        if iteration == MAX_TOOL_ITERATIONS - 2:
            log.warning(
                "Approaching max tool iterations (%d), forcing text response",
                MAX_TOOL_ITERATIONS,
            )
            messages.append({
                "role": "system",
                "content": "You've used many tools. Please provide your final response to the patient now.",
            })

    log.error(
        "Agent loop exhausted %d iterations without a final response", MAX_TOOL_ITERATIONS
    )
    return "I apologize, but I'm having trouble processing your request. Could you please try again?"


async def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your .env file first.")

    log.info("Starting MedBook assistant (Neo4j: %s)", uri)

    db = HospitalDB(uri, user, password)
    memory = GraphMemory(uri, user, password)
    llm = LLM()

    print("Connecting to Neo4j...")
    await db.connect()
    await memory.setup()
    log.info("Connected to Neo4j and Graphiti memory.")

    # Initialize conversation with system prompt
    tz_name = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d (%A)")
    current_time = now.strftime("%H:%M")
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(
            today=today, current_time=current_time, timezone=tz_name
        )},
    ]

    log.info("Session started | date=%s time=%s tz=%s", today, current_time, tz_name)

    print("\n" + "=" * 60)
    print("  MedBook -- Doctor Appointment Booking Assistant")
    print("=" * 60)
    print("\nType 'quit' to exit, 'reset' to start a new conversation.\n")

    WELCOME_MESSAGE = (
        "Welcome to City Care Hospital! 🏥\n"
        "I'm MedBook, your appointment booking assistant.\n"
        "How may I help you today? You can tell me your symptoms, "
        "name a doctor or speciality, or ask to see your existing bookings."
    )
    print(f"bot > {WELCOME_MESSAGE}\n")
    log.info("Welcome message displayed to user.")

    try:
        while True:
            try:
                user_message = input("you > ").strip()
            except EOFError:
                log.info("EOF received, shutting down.")
                break
            except KeyboardInterrupt:
                print("\n\nInterrupted. Goodbye!")
                log.info("KeyboardInterrupt received, shutting down gracefully.")
                break

            if user_message.lower() in {"quit", "exit", "bye"}:
                log.info("User ended session with: '%s'", user_message)
                break
            if not user_message:
                continue

            if user_message.lower() == "reset":
                now = datetime.now(ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata")))
                today = now.strftime("%Y-%m-%d (%A)")
                current_time = now.strftime("%H:%M")
                tz_name = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT.format(
                        today=today, current_time=current_time, timezone=tz_name
                    )},
                ]
                log.info("Conversation reset by user.")
                print("\n--- Conversation reset. Start fresh! ---\n")
                continue

            try:
                reply = await agent_loop(llm, db, memory, messages, user_message)
            except Exception as exc:
                log.exception("Unexpected error in agent loop: %s", exc)
                reply = "Sorry, something went wrong on my end. Please try again."

            print(f"\nbot > {reply}\n")

    finally:
        log.info("Closing connections...")
        await db.close()
        await memory.close()
        log.info("Session ended.")
        print("\nGoodbye! Your bookings are saved in Neo4j.")


if __name__ == "__main__":
    asyncio.run(main())
