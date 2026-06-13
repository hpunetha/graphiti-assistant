"""
seed_demo.py — Populate the graph with a scripted conversation.

Great for recording a demo: run this, then open Neo4j Browser and watch the
graph that got built. The script deliberately includes a CHANGED FACT (the user
moves cities) so you can show Graphiti's temporal model superseding the old
fact instead of deleting it.

Usage:
    docker compose up -d
    python -m app.seed_demo
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from app.memory import GraphMemory, quiet_graphiti_logs

load_dotenv()
quiet_graphiti_logs()

# A small, relationship-rich story about me (Miguel Otero Pedrido).
# Each line becomes an episode, and the last one changes a fact so you can
# watch the temporal model supersede it rather than delete it.
EPISODES = [
    "My name is Miguel Otero Pedrido. I'm a physicist turned machine learning "
    "engineer with over 6 years of experience, and I used to live in Almería, "
    "Spain.",
    "I'm the founder of The Neural Maze, a newsletter and YouTube channel where "
    "I teach ML and AI engineering step by step with code, articles, and video "
    "tutorials. On GitHub I go by MichaelisTrofficus.",
    "I focus on building AI systems that actually work in production — ML "
    "engineering, MLOps, and GenAI. My everyday tech stack is Python with "
    "PyTorch, LangChain, and Hugging Face, deployed on AWS and GCP with Docker "
    "and Kubernetes.",
    "My most popular open-source project is the agentic-patterns-course, where "
    "I implement four agentic patterns from scratch. It has over 1.7k stars on "
    "GitHub.",
    # Kept as short, single-relationship sentences so the extractor reliably
    # emits both the entity nodes and the edges between them.
    "I co-created the Grokking Agents course with Luis Serrano.",
    "I also co-created the Grokking Agents course with Antonio Zarauz.",
    "Antonio Zarauz and I collaborate on educational content for AI engineers "
    "under The Neural Maze Substack.",
    # --- The fact change: watch the temporal model handle this ---
    "Update: I recently moved. I no longer live in Almería — "
    "I now live in Vigo, the city where I was born.",
]


async def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "demodemo123")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your .env file first.")

    memory = GraphMemory(uri, user, password)
    print("Building indices (first run only)...")
    await memory.setup()

    user_id = "demo_user"
    try:
        for i, text in enumerate(EPISODES, start=1):
            print(f"[{i}/{len(EPISODES)}] ingesting: {text[:60]}...")
            await memory.remember(text, user_id=user_id)
        print("\nDone. Open http://localhost:7474 and run:")
        print("    MATCH (n)-[r]->(m) RETURN n, r, m")
        print("\nNotice the LIVES_IN edge to Almería is superseded by Vigo,")
        print("rather than deleted — that's the temporal knowledge graph at work.")
    finally:
        await memory.close()


if __name__ == "__main__":
    asyncio.run(main())
