from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.core.config import Settings
from app.utils.text import clean_text


class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = settings.openai_base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }

    async def embeddings(self, texts: list[str], model: str) -> list[list[float]]:
        payload = {"model": model, "input": [clean_text(t, 8000) for t in texts]}
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.base}/embeddings", headers=self.headers, json=payload)
            response.raise_for_status()
            data = response.json().get("data", [])
            return [item["embedding"] for item in data]

    async def rerank(self, query: str, documents: list[str], top_n: int, model: str) -> list[dict[str, Any]]:
        payload = {
            "model": model,
            "query": clean_text(query, 2000),
            "documents": [clean_text(doc, 3000) for doc in documents],
            "top_n": top_n,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.base}/rerank", headers=self.headers, json=payload)
            response.raise_for_status()
            body = response.json()
            result = body.get("results") or body.get("data") or []
            normalized: list[dict[str, Any]] = []
            for item in result:
                normalized.append(
                    {
                        "index": item.get("index", 0),
                        "score": item.get("relevance_score", item.get("score", 0.0)),
                    }
                )
            return normalized

    async def stream_chat(self, model: str, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{self.base}/chat/completions", headers=self.headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ").strip()
                    if data == "[DONE]":
                        break
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (
                        parsed.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        yield delta
