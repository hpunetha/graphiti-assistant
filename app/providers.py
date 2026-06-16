"""
providers.py — Factory functions for Graphiti's LLM and embedder clients.

Configure via environment variables (see .env.example).  The rest of the app
only imports `build_llm_client` and `build_embedder` — swapping providers is
a config change, not a code change.

Supported LLM providers  (GRAPHITI_LLM_PROVIDER):
  openai    — OpenAI API (default)
  anthropic — Anthropic Claude  (needs: pip install graphiti-core[anthropic])
  gemini    — Google Gemini     (needs: pip install graphiti-core[google-genai])
  groq      — Groq              (needs: pip install graphiti-core[groq])
  ollama    — Ollama local server (OpenAI-compatible, no extra install)

Supported embedder providers  (GRAPHITI_EMBEDDER_PROVIDER):
  openai       — OpenAI embeddings (default)
  ollama       — Ollama local server (OpenAI-compatible, no extra install)
  gemini       — Google Gemini embeddings
  voyage       — Voyage AI          (needs: pip install graphiti-core[voyage])
  huggingface  — Any HuggingFace model via sentence-transformers
                 (needs: pip install sentence-transformers)
                 Works with EmbeddingGemma, nomic-ai, BGE, E5, etc.
                 Models supporting MRL let you choose any EMBEDDING_DIM.
"""

from __future__ import annotations

import asyncio
import functools
import os
from collections.abc import Iterable

from graphiti_core.embedder import EmbedderClient, OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client import LLMClient, LLMConfig, OpenAIClient


class HuggingFaceEmbedder(EmbedderClient):
    """Embedder backed by any sentence-transformers compatible HuggingFace model.

    sentence-transformers.encode() is synchronous, so calls are dispatched to
    a thread-pool executor to stay non-blocking inside asyncio.

    Models that support Matryoshka Representation Learning (MRL) — such as
    google/gemma-embedding-001 and nomic-ai/nomic-embed-text-v1 — allow you to
    truncate the output to any EMBEDDING_DIM without quality loss.
    """

    def __init__(self, model_name: str, dim: int) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for the huggingface embedder. "
                "Install it with: pip install sentence-transformers"
            ) from None
        self._model = SentenceTransformer(model_name, trust_remote_code=True)
        self._dim = dim

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return [e[: self._dim].tolist() for e in embeddings]

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        text = input_data if isinstance(input_data, str) else str(input_data)
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, functools.partial(self._encode_sync, [text])
        )
        return results[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._encode_sync, input_data_list)
        )


def build_llm_client() -> LLMClient:
    """Return a Graphiti LLMClient based on GRAPHITI_LLM_PROVIDER."""
    provider = os.getenv("GRAPHITI_LLM_PROVIDER", "openai").lower()
    model = os.getenv("GRAPHITI_LLM_MODEL")
    base_url = os.getenv("GRAPHITI_LLM_BASE_URL")
    api_key = os.getenv("GRAPHITI_LLM_API_KEY")

    if provider == "openai":
        config = LLMConfig(
            api_key=api_key or os.environ["OPENAI_API_KEY"],
            model=model or "gpt-4o-mini",
            base_url=base_url,
        )
        return OpenAIClient(config=config)

    if provider == "anthropic":
        from graphiti_core.llm_client.anthropic_client import AnthropicClient

        config = LLMConfig(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
            model=model or "claude-3-5-haiku-latest",
        )
        return AnthropicClient(config=config)

    if provider == "gemini":
        from graphiti_core.llm_client.gemini_client import GeminiClient

        config = LLMConfig(
            api_key=api_key or os.getenv("GEMINI_API_KEY"),
            model=model or "gemini-2.0-flash",
        )
        return GeminiClient(config=config)

    if provider == "groq":
        from graphiti_core.llm_client.groq_client import GroqClient

        config = LLMConfig(
            api_key=api_key or os.getenv("GROQ_API_KEY"),
            model=model or "llama-3.3-70b-versatile",
        )
        return GroqClient(config=config)

    if provider == "ollama":
        # Ollama exposes an OpenAI-compatible API — use OpenAIGenericClient
        # which avoids structured-output modes that local models don't support.
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        config = LLMConfig(
            api_key=api_key or "ollama",
            model=model or "llama3.2",
            base_url=base_url or "http://localhost:11434/v1",
        )
        return OpenAIGenericClient(config=config)

    raise ValueError(
        f"Unknown GRAPHITI_LLM_PROVIDER={provider!r}. "
        "Choose: openai | anthropic | gemini | groq | ollama"
    )


def build_embedder() -> EmbedderClient:
    """Return a Graphiti EmbedderClient based on GRAPHITI_EMBEDDER_PROVIDER.

    IMPORTANT: EMBEDDING_DIM must match the model's actual output dimension.
    Changing dimension requires rebuilding the Neo4j vector index from scratch.
    """
    provider = os.getenv("GRAPHITI_EMBEDDER_PROVIDER", "openai").lower()
    model = os.getenv("GRAPHITI_EMBEDDER_MODEL")
    base_url = os.getenv("GRAPHITI_EMBEDDER_BASE_URL")
    api_key = os.getenv("GRAPHITI_EMBEDDER_API_KEY")

    if provider == "openai":
        config = OpenAIEmbedderConfig(
            embedding_model=model or "text-embedding-3-small",
            api_key=api_key or os.environ["OPENAI_API_KEY"],
            base_url=base_url,
        )
        return OpenAIEmbedder(config=config)

    if provider == "ollama":
        # Ollama's /v1/embeddings endpoint is OpenAI-compatible
        config = OpenAIEmbedderConfig(
            embedding_model=model or "nomic-embed-text",
            api_key=api_key or "ollama",
            base_url=base_url or "http://localhost:11434/v1",
        )
        return OpenAIEmbedder(config=config)

    if provider == "gemini":
        from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig

        config = GeminiEmbedderConfig(
            embedding_model=model or "models/text-embedding-004",
            api_key=api_key or os.getenv("GEMINI_API_KEY"),
        )
        return GeminiEmbedder(config=config)

    if provider == "voyage":
        from graphiti_core.embedder.voyage import VoyageAIEmbedder, VoyageAIEmbedderConfig

        config = VoyageAIEmbedderConfig(
            embedding_model=model or "voyage-3-lite",
            api_key=api_key or os.getenv("VOYAGE_API_KEY"),
        )
        return VoyageAIEmbedder(config=config)

    if provider == "huggingface":
        hf_model = model or "google/gemma-embedding-001"
        dim = int(os.getenv("EMBEDDING_DIM", 1024))
        return HuggingFaceEmbedder(hf_model, dim)

    raise ValueError(
        f"Unknown GRAPHITI_EMBEDDER_PROVIDER={provider!r}. "
        "Choose: openai | ollama | gemini | voyage | huggingface"
    )
