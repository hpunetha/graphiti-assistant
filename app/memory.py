"""
memory.py — A temporal-knowledge-graph memory layer over Graphiti + Neo4j.

Exposes four public methods:

    remember(text, user_id, ...)         — ingest a fact/episode (typed or plain)
    auto_ingest(user_msg, reply, phone)  — background: ingest a full conversation turn
    recall_patient_facts(query, phone)   — typed retrieval scoped to patient namespace
    recall_medical_knowledge(query)      — typed retrieval scoped to hospital namespace

The lower-level recall() is kept for backward compatibility.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

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
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.search.search_filters import SearchFilters

from app.ontology import (
    EXTRACTION_INSTRUCTIONS,
    PATIENT_EDGE_TYPE_MAP,
    PATIENT_EDGE_TYPES,
    PATIENT_ENTITY_TYPES,
)
from app.providers import build_embedder, build_llm_client


def quiet_graphiti_logs() -> None:
    """Silence Graphiti/Neo4j's benign first-run log noise."""
    logging.getLogger("graphiti_core").setLevel(logging.CRITICAL)
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


# Legacy search config kept as fallback for the plain recall() method.
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

# Labels used to narrow patient-fact retrieval to clinically relevant nodes.
# Uses KG prefix to distinguish Graphiti's EntityNodes from hospital-api's
# transactional (:Patient), (:Doctor) etc. nodes in the same Neo4j database.
_PATIENT_NODE_LABELS = [
    "KGPatient", "KGSymptom", "KGAllergy", "KGPreference", "KGFamilyMember", "KGAppointment",
]

# Labels used to narrow hospital medical-knowledge retrieval.
_HOSPITAL_NODE_LABELS = ["KGDoctor", "KGSymptom"]


class GraphMemory:
    """Temporal knowledge-graph memory backed by Graphiti + Neo4j."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self.client = Graphiti(
            uri,
            user,
            password,
            llm_client=build_llm_client(),
            embedder=build_embedder(),
        )

    async def setup(self) -> None:
        """One-time: create the indices and constraints Graphiti relies on."""
        await self.client.build_indices_and_constraints()

    # ── Ingestion ────────────────────────────────────────────────────────────

    async def remember(
        self,
        text: str,
        user_id: str,
        source_desc: str = "conversation",
        entity_types: dict[str, Any] | None = None,
        edge_types: dict[str, Any] | None = None,
        edge_type_map: dict[tuple[str, str], list[str]] | None = None,
        instructions: str | None = None,
    ) -> None:
        """Ingest a text fact or observation into the knowledge graph.

        When entity_types / edge_types / edge_type_map are supplied, Graphiti's
        extraction LLM is constrained to the specified domain schema, producing
        precise typed nodes and edges instead of generic Entity / relates_to.
        """
        await self.client.add_episode(
            name=f"{user_id}_{datetime.now(timezone.utc).timestamp()}",
            episode_body=text,
            source=EpisodeType.text,
            source_description=source_desc,
            reference_time=datetime.now(timezone.utc),
            group_id=user_id,
            entity_types=entity_types,
            edge_types=edge_types,
            edge_type_map=edge_type_map,
            custom_extraction_instructions=instructions,
        )

    async def auto_ingest(
        self,
        user_message: str,
        assistant_reply: str,
        patient_phone: str,
    ) -> None:
        """Fire-and-forget: fold a completed conversation turn into the graph.

        Uses EpisodeType.message ("Patient: …\nAssistant: …") so Graphiti
        understands the conversational structure and extracts facts from both
        sides. Runs with the full PATIENT ontology for precise typed extraction.
        """
        episode_body = f"Patient: {user_message}\nAssistant: {assistant_reply}"
        await self.client.add_episode(
            name=f"{patient_phone}_turn_{datetime.now(timezone.utc).timestamp()}",
            episode_body=episode_body,
            source=EpisodeType.message,
            source_description="conversation_turn",
            reference_time=datetime.now(timezone.utc),
            group_id=patient_phone,
            entity_types=PATIENT_ENTITY_TYPES,
            edge_types=PATIENT_EDGE_TYPES,
            edge_type_map=PATIENT_EDGE_TYPE_MAP,
            custom_extraction_instructions=EXTRACTION_INSTRUCTIONS,
        )

    # ── Retrieval ─────────────────────────────────────────────────────────────

    async def recall_patient_facts(
        self, query: str, patient_phone: str, limit: int = 8
    ) -> list[str]:
        """Retrieve patient-specific facts from their graph namespace.

        Uses COMBINED_HYBRID_SEARCH_RRF (edges + nodes + episodes) and filters
        to patient-relevant entity labels so hospital medical-knowledge nodes
        from the "hospital" group don't bleed into patient recall.
        """
        config = deepcopy(COMBINED_HYBRID_SEARCH_RRF)
        config.limit = limit
        results = await self.client.search_(
            query=query,
            config=config,
            group_ids=[patient_phone],
            search_filter=SearchFilters(node_labels=_PATIENT_NODE_LABELS),
        )
        edge_facts = [r.fact for r in results.edges]
        node_summaries = [n.summary for n in results.nodes if n.summary]
        return (edge_facts + node_summaries)[:limit]

    async def recall_medical_knowledge(
        self, query: str, limit: int = 10
    ) -> list[str]:
        """Retrieve medical knowledge (symptom→speciality mappings, doctor profiles).

        Scoped to the "hospital" namespace and filtered to Doctor/Symptom nodes
        so patient-personal data from individual namespaces is excluded.
        """
        config = deepcopy(COMBINED_HYBRID_SEARCH_RRF)
        config.limit = limit
        results = await self.client.search_(
            query=query,
            config=config,
            group_ids=["hospital"],
            search_filter=SearchFilters(node_labels=_HOSPITAL_NODE_LABELS),
        )
        edge_facts = [r.fact for r in results.edges]
        node_summaries = [n.summary for n in results.nodes if n.summary]
        return (edge_facts + node_summaries)[:limit]

    async def recall(self, query: str, user_id: str, limit: int = 8) -> list[str]:
        """Untyped hybrid search — kept for backward compatibility.

        Prefer recall_patient_facts() or recall_medical_knowledge() for new code.
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
