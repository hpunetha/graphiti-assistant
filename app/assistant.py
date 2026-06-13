"""
assistant.py — A personal assistant with temporal knowledge-graph memory.

Run an interactive chat. Every exchange becomes an episode that Graphiti folds
into a Neo4j knowledge graph; every new question is answered using a hybrid
search over that graph. Over a few conversations it builds a real model of you:
who you know, where you live, what you're into, and how it all connects.

While you chat, open Neo4j Browser at http://localhost:7474 (neo4j /
demodemo123) and run:

    MATCH (n)-[r]->(m) RETURN n, r, m

...to watch the graph assemble itself node by node.

Usage:
    1. docker compose up -d           # start Neo4j
    2. cp .env.example .env           # then add your OpenAI key
    3. pip install -r requirements.txt
    4. python -m app.assistant        # chat! (type 'quit' to exit)
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from app.llm import LLM
from app.memory import GraphMemory, quiet_graphiti_logs

load_dotenv()
quiet_graphiti_logs()

SYSTEM_TEMPLATE = (
    "You are a warm, concise personal assistant with long-term memory. "
    "Use the FACTS below to personalise your reply and refer to people and "
    "details the user has told you about before. If two facts conflict, trust "
    "the most recent one. Never invent facts you don't actually have.\n\n"
    "FACTS I REMEMBER ABOUT THIS USER:\n{context}"
)


async def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your .env file first.")

    memory = GraphMemory(uri, user, password)
    llm = LLM()

    print("Connecting to the graph and building indices (first run only)...")
    await memory.setup()

    # In a real multi-tenant app this would come from auth. Here we hardcode
    # one user so the demo is reproducible.
    user_id = "demo_user"

    print("\nPersonal assistant ready. Tell me about yourself!")
    print("Tip: open http://localhost:7474 and run  MATCH (n)-[r]->(m) RETURN n, r, m")
    print("Type 'quit' to exit.\n")

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

            # 1. RECALL: search the graph for what we already know.
            facts = await memory.recall(user_message, user_id=user_id)
            context = "\n".join(f"- {f}" for f in facts) or "(nothing yet)"

            # 2. GENERATE: answer, grounded in the retrieved facts.
            reply = llm.chat(
                system=SYSTEM_TEMPLATE.format(context=context),
                user=user_message,
            )
            print(f"bot > {reply}\n")

            # 3. REMEMBER: store the exchange so the graph keeps growing.
            #    (This triggers LLM-based entity/relationship extraction, so it
            #    takes a moment — that's Graphiti building the graph.)
            print("      ...updating memory graph...", end="\r")
            await memory.remember(
                f"User said: {user_message}\nAssistant replied: {reply}",
                user_id=user_id,
            )
            print(" " * 40, end="\r")  # clear the status line
    finally:
        await memory.close()
        print("\nGoodbye! Your memory graph is still in Neo4j — go explore it.")


if __name__ == "__main__":
    asyncio.run(main())
