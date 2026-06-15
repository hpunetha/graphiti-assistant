"""
memory.py — A temporal-knowledge-graph memory layer over Graphiti + Neo4j.

This is the heart of the example. It wraps `graphiti-core` so the rest of the
app only has to think in terms of two verbs:

    remember(text, user_id)  -> fold a new exchange into the graph as an episode
    recall(query, user_id)   -> hybrid (semantic + keyword + graph) search

Graphiti handles the hard parts for us: extracting entities and relationships
from raw text with an LLM, reconciling them against what is already in the
graph, time-stamping every edge, and superseding (rather than deleting) facts
that have changed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from graphiti_core.search.search_config import (
    EdgeReranker,
    EdgeSearchConfig,
    EdgeSearchMethod,
    NodeReranker,
    NodeSearchConfig,
    NodeSearchMethod,
    SearchConfig,
)


def quiet_graphiti_logs() -> None:
    """Silence Graphiti/Neo4j's benign first-run log noise.

    Two harmless-but-loud sources get muted:
      * `graphiti_core` logs every `CREATE INDEX ... IF NOT EXISTS` that hits
        an already-existing index as an "error" (EquivalentSchemaRuleAlreadyExists).
      * the Neo4j driver emits `01N52` notifications when Graphiti's internal
        dedup searches reference properties (e.g. `name_embedding`) that don't
        exist yet on a fresh graph.
    Neither indicates a real problem, so we raise their log thresholds.
    """
    logging.getLogger("graphiti_core").setLevel(logging.CRITICAL)
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


# Custom search config with no minimum score — default 0.6 threshold silently
# drops low-similarity facts. We search both edges AND nodes because Graphiti
# stores entity summaries (e.g. "prefers evening slots") on EntityNode.summary,
# not just on edges.
_PATIENT_SEARCH_CONFIG = SearchConfig(
    edge_config=EdgeSearchConfig(
        search_methods=[EdgeSearchMethod.bm25, EdgeSearchMethod.cosine_similarity],
        reranker=EdgeReranker.rrf,
        sim_min_score=0.0,
    ),
    node_config=NodeSearchConfig(
        search_methods=[NodeSearchMethod.bm25, NodeSearchMethod.cosine_similarity],
        reranker=NodeReranker.rrf,
        sim_min_score=0.0,
    ),
)


class GraphMemory:
    """Temporal knowledge-graph memory backed by Graphiti + Neo4j."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        # Graphiti connects to Neo4j over Bolt and defaults to OpenAI for the
        # extraction LLM + embeddings (set OPENAI_API_KEY in the environment).
        self.client = Graphiti(uri, user, password)

    async def setup(self) -> None:
        """One-time: create the indices and constraints Graphiti relies on."""
        await self.client.build_indices_and_constraints()

    async def remember(
        self,
        text: str,
        user_id: str,
        source_desc: str = "conversation",
    ) -> None:
        """Add a new exchange to the graph as an episode.

        Graphiti will extract entities (people, places, things), infer the
        relationships between them, and merge everything into the existing
        graph with temporal validity on each edge.
        """
        await self.client.add_episode(
            name=f"{user_id}_{datetime.now(timezone.utc).timestamp()}",
            episode_body=text,
            source=EpisodeType.text,
            source_description=source_desc,
            reference_time=datetime.now(timezone.utc),
            group_id=user_id,  # isolate each user's memory into its own namespace
        )

    async def recall(self, query: str, user_id: str, limit: int = 8) -> list[str]:
        """Retrieve relevant facts using Graphiti's hybrid search.

        This combines vector (semantic) similarity, BM25 (keyword) matching,
        and graph traversal — and it does NOT call an LLM at query time, so it
        is fast.

        We use sim_min_score=0.0 (vs the default 0.6) so that preference and
        relationship facts aren't silently dropped by the cosine threshold.
        """
        _PATIENT_SEARCH_CONFIG.limit = limit
        results = await self.client.search_(
            query=query,
            config=_PATIENT_SEARCH_CONFIG,
            group_ids=[user_id],
        )
        edge_facts = [r.fact for r in results.edges]
        node_summaries = [n.summary for n in results.nodes if n.summary]
        return (edge_facts + node_summaries)[:limit]

    async def close(self) -> None:
        await self.client.close()
