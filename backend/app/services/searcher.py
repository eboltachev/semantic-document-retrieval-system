from __future__ import annotations

from collections import defaultdict
import re
from urllib.parse import urlparse, urlunparse

from app.core.config import Settings
from app.services.ai_client import OpenAICompatibleClient
from app.services.opensearch_store import OpenSearchStore
from app.utils.text import clean_text


class SearchService:
    def __init__(self, settings: Settings, store: OpenSearchStore, ai: OpenAICompatibleClient):
        self.settings = settings
        self.store = store
        self.ai = ai

    def _rrf(self, vector_hits: list[dict], keyword_hits: list[dict], k: int = 60) -> list[dict]:
        scores: dict[str, float] = defaultdict(float)
        docs: dict[str, dict] = {}
        for rank, hit in enumerate(vector_hits, 1):
            doc_id = hit.get("_id")
            scores[doc_id] += 1.0 / (k + rank)
            docs[doc_id] = hit
        for rank, hit in enumerate(keyword_hits, 1):
            doc_id = hit.get("_id")
            scores[doc_id] += 1.0 / (k + rank)
            docs[doc_id] = hit
        sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
        return [docs[i] for i in sorted_ids]

    def _derive_source_title(self, source: dict) -> str:
        section_title = clean_text(source.get("section_title", ""), 140)
        base_title = clean_text(source.get("title", ""), 140)
        if section_title:
            if base_title and section_title.lower() != base_title.lower():
                # return f"{base_title} → {section_title}"
                return f"{base_title}"
            return section_title

        text = clean_text(source.get("text", ""), 400)
        first_line = ""
        for line in text.split("\n"):
            candidate = line.strip()
            if len(candidate) > 4:
                first_line = candidate
                break

        if first_line and first_line.lower() != base_title.lower():
            return first_line[:140]
        if base_title:
            return base_title

        path = urlparse(source.get("url", "")).path.strip("/")
        if path:
            return path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")[:140]
        return "Источник"

    def _canonicalize_source_url(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        cleaned = parsed._replace(query="", fragment="")
        canonical = urlunparse(cleaned)
        if canonical.endswith("/") and len(canonical) > len(f"{parsed.scheme}://{parsed.netloc}/"):
            canonical = canonical[:-1]
        return canonical

    def _make_source_key(self, source_title: str, source_url: str) -> str:
        normalized_title = re.sub(r"\W+", " ", source_title.lower()).strip()
        normalized_title = re.sub(r"\s+", " ", normalized_title)
        canonical_url = self._canonicalize_source_url(source_url)
        return f"{canonical_url}|{normalized_title}"

    async def run(self, query: str, event_cb):
        await event_cb("status", {"message": "Проверяю состояние индекса"})
        if not self.store.index_exists():
            raise RuntimeError("Индекс не готов")

        await event_cb("status", {"message": "Формирую эмбеддинг запроса"})
        embedding = (await self.ai.embeddings([query], self.settings.embed_model_name))[0]

        await event_cb("status", {"message": "Выполняю поиск в векторной БД"})
        vector_hits = self.store.vector_search(embedding, self.settings.default_retrieve_k)

        await event_cb("status", {"message": "Выполняю дополнительный лексический поиск"})
        keyword_hits = self.store.keyword_search(query, self.settings.default_retrieve_k)

        await event_cb("status", {"message": "Объединяю кандидатов"})
        fused = self._rrf(vector_hits, keyword_hits)[: self.settings.default_retrieve_k]
        if not fused:
            raise RuntimeError("Релевантные документы не найдены")

        await event_cb("status", {"message": "Выполняю реранжирование"})
        docs = [hit["_source"]["text"] for hit in fused]
        reranked = await self.ai.rerank(query, docs, self.settings.default_rerank_k, self.settings.rerank_model_name)
        top_hits = []
        for item in reranked:
            idx = item.get("index", 0)
            if 0 <= idx < len(fused):
                top_hits.append(fused[idx])
        if not top_hits:
            top_hits = fused[: self.settings.default_rerank_k]

        context_parts = []
        sources = []
        seen_sources: set[str] = set()
        total = 0
        for hit in top_hits:
            source = hit["_source"]
            text = clean_text(source["text"], 2000)
            if total + len(text) > self.settings.max_context_chars:
                break
            total += len(text)
            context_parts.append(f"[{len(context_parts)+1}] {source['title']}\n{text}")
            source_title = self._derive_source_title(source)
            source_key = self._make_source_key(source_title, source["url"])
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            canonical_url = self._canonicalize_source_url(source["url"])
            sources.append(
                {
                    "title": source_title,
                    "url": canonical_url,
                    "preview": text[:220],
                }
            )

        await event_cb("status", {"message": "Формирую ответ"})
        system = (
            "Ты отвечаешь только по предоставленному контексту. "
            "Если данных недостаточно, честно скажи об этом. "
            "Отвечай на русском языке. Не используй markdown-таблицы и не вставляй URL в основной текст."
        )
        context_blob = "\n\n".join(context_parts)
        user_prompt = (
            f"Вопрос: {query}\n\n"
            f"Контекст:\n{context_blob}\n\n"
            "Сформируй краткий точный ответ по контексту."
        )

        async for token in self.ai.stream_chat(self.settings.llm_model_name, system, user_prompt):
            await event_cb("token", {"text": token})

        await event_cb("sources", {"items": sources})
        await event_cb("status", {"message": "Завершаю"})
