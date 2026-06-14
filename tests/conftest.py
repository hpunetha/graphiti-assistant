"""
tests/conftest.py

Shared pytest configuration for the MedBook test suite.

Key responsibility: suppress the FastAPI lifespan (startup/shutdown events)
so tests never try to connect to a real Neo4j instance. The global
db / memory / llm objects are replaced with mocks in each test module via
the patch_globals fixture defined in test_api.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(scope="session", autouse=True)
def suppress_lifespan():
    """
    Replace the lifespan context manager in app.api so that startup code
    (Neo4j connect, Graphiti setup) is never executed during tests.
    """

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    with patch("app.api.lifespan", new=_noop_lifespan):
        yield
