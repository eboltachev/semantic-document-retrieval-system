from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.services.ai_client import OpenAICompatibleClient
from app.services.crawler import SiteCrawler
from app.services.opensearch_store import OpenSearchStore
from app.utils.hash import make_chunk_id
from app.utils.text import clean_text, extract_section_title, split_paragraph_chunks


@dataclass
class ChunkDoc:
    chunk_id: str
    url: str
    title: str
    section_title: str
    source_type: str
    chunk_index: int
    text: str


class IndexService:
    def __init__(self, settings: Settings, store: OpenSearchStore, ai: OpenAICompatibleClient, src_base_url: str | None = None):
        self.settings = settings
        self.store = store
        self.ai = ai
        self.crawler = SiteCrawler(settings, src_base_url=src_base_url)

    async def rebuild(self, status_cb) -> None:
        await status_cb("Удаляю старый индекс", 5)
        self.store.delete_index()

        await status_cb("Краулинг источников", 10)
        docs = await self.crawler.crawl(status_cb)
        if not docs:
            raise RuntimeError("Краулер не вернул документов")

        await status_cb("Чанкинг документов", 55)
        chunks: list[ChunkDoc] = []
        for doc in docs:
            chunk_texts = split_paragraph_chunks(
                clean_text(doc.text, 120_000),
                self.settings.chunk_size,
                self.settings.chunk_overlap,
            )
            for i, text in enumerate(chunk_texts):
                chunks.append(
                    ChunkDoc(
                        chunk_id=make_chunk_id(doc.url, i, text),
                        url=doc.url,
                        title=doc.title,
                        section_title=extract_section_title(text, doc.title),
                        source_type=doc.source_type,
                        chunk_index=i,
                        text=text,
                    )
                )

        if not chunks:
            raise RuntimeError("Не удалось сформировать чанки")

        await status_cb("Получаю эмбеддинги", 65)
        sample_vector = (await self.ai.embeddings([chunks[0].text], self.settings.embed_model_name))[0]

        await status_cb("Создаю индекс", 75)
        self.store.create_index(len(sample_vector))

        await status_cb("Индексация чанков в OpenSearch", 80)
        batch_size = 16
        documents = []
        total_batches = max(1, (len(chunks) + batch_size - 1) // batch_size)
        for batch_no, idx in enumerate(range(0, len(chunks), batch_size), start=1):
            part = chunks[idx : idx + batch_size]
            vectors = await self.ai.embeddings([c.text for c in part], self.settings.embed_model_name)
            for chunk, vector in zip(part, vectors, strict=True):
                documents.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "url": chunk.url,
                        "title": chunk.title,
                        "section_title": chunk.section_title,
                        "source_type": chunk.source_type,
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                        "embedding": vector,
                    }
                )
            progress = min(98, 80 + int((batch_no / total_batches) * 18))
            await status_cb("Индексация чанков в OpenSearch", progress)
        self.store.bulk_upsert(documents)
        await status_cb("Индексация завершена", 100)
