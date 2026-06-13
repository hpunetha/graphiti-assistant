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

from dotenv import load_dotenv

from app.db import HospitalDB
from app.llm import LLM
from app.memory import GraphMemory, quiet_graphiti_logs
from app.tools import TOOL_SCHEMAS, execute_tool

load_dotenv()
quiet_graphiti_logs()

SYSTEM_PROMPT = """\
You are MedBook, a friendly and professional hospital appointment booking assistant.
Your job is to help patients find the right doctor and book appointments.

Today's date is {today}.

## Your Workflow
1. **Identify the patient** — Always start by asking for their phone number.
   Use the identify_patient tool to look them up. If they're new, collect their
   name, age, and gender to register them.

2. **Understand their need** — They might:
   - Name a specific doctor → use search_doctors with the name
   - Mention a speciality → use search_doctors with the speciality
   - Describe symptoms → use suggest_speciality to find the right specialist
   - Ask to see their bookings → use get_my_bookings
   - Want to cancel → use cancel_booking

3. **Find available slots** — Once a doctor is identified, ask what date
   works for them and use get_available_slots.

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
    messages.append({"role": "user", "content": user_message})

    for iteration in range(MAX_TOOL_ITERATIONS):
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
            return assistant_msg.content or ""

        # Execute each tool call and append results
        for tool_call in assistant_msg.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            print(f"      [tool] {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:80]})")

            result = await execute_tool(fn_name, fn_args, db, memory)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

        # If it's the last iteration, tell the model to respond in text
        if iteration == MAX_TOOL_ITERATIONS - 2:
            messages.append({
                "role": "system",
                "content": "You've used many tools. Please provide your final response to the patient now.",
            })

    return "I apologize, but I'm having trouble processing your request. Could you please try again?"


async def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your .env file first.")

    db = HospitalDB(uri, user, password)
    memory = GraphMemory(uri, user, password)
    llm = LLM()

    print("Connecting to Neo4j...")
    await db.connect()
    await memory.setup()

    # Initialize conversation with system prompt
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
    ]

    print("\n" + "=" * 60)
    print("  MedBook -- Doctor Appointment Booking Assistant")
    print("=" * 60)
    print("\nType your message to start booking an appointment.")
    print("Type 'quit' to exit, 'reset' to start a new conversation.\n")

    try:
        while True:
            try:
                user_message = input("you > ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if user_message.lower() in {"quit", "exit", "bye"}:
                break
            if not user_message:
                continue

            if user_message.lower() == "reset":
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
                ]
                print("\n--- Conversation reset. Start fresh! ---\n")
                continue

            reply = await agent_loop(llm, db, memory, messages, user_message)
            print(f"\nbot > {reply}\n")

    finally:
        await db.close()
        await memory.close()
        print("\nGoodbye! Your bookings are saved in Neo4j.")


if __name__ == "__main__":
    asyncio.run(main())
