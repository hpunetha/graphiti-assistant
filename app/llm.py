"""
llm.py — OpenAI client with function-calling support for the agentic loop.

Provides two modes:
  - chat(): simple single-turn completion (used for non-agentic cases)
  - chat_with_tools(): returns raw ChatCompletion so the agent loop can
    inspect tool_calls and execute them iteratively
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletion


class LLM:
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = model

    def chat(self, system: str, user: str) -> str:
        """Simple single-turn completion (no tool calling)."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.4,
    ) -> ChatCompletion:
        """Completion with tool definitions. Returns the raw ChatCompletion
        so the agent loop can inspect tool_calls.

        Args:
            messages: Full conversation history (system + user + assistant + tool).
            tools: OpenAI function-calling tool schemas.
            temperature: Lower for more deterministic tool selection.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        return self.client.chat.completions.create(**kwargs)
