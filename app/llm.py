"""
llm.py — A tiny wrapper around the OpenAI client for generating replies.

Deliberately minimal: no agent framework, no orchestration magic. Just a
single chat completion so you can see exactly how the retrieved graph facts
flow into the model's context.
"""

from __future__ import annotations

import os

from openai import OpenAI


class LLM:
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = model

    def chat(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
