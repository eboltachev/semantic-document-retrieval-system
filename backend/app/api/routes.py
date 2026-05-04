from __future__ import annotations

import asyncio

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.core.config import allowed_extensions, archive_extensions
from app.schemas.indexing import AppStateResponse, IndexRebuildRequest, TaskCreateResponse
from app.schemas.search import SearchRequest
from app.services.ai_client import OpenAICompatibleClient
from app.services.crawler import CrawledDocument
from app.services.indexer import IndexService
from app.services.opensearch_store import OpenSearchStore
from app.services.document_loader import ArchiveDocumentLoader
from app.services.searcher import SearchService
from app.services.task_stream import StreamRegistry


router = APIRouter(prefix="/api")

stream_registry = StreamRegistry()
index_lock = asyncio.Lock()
search_lock = asyncio.Lock()
state = {"indexing_running": False, "index_ready": False}


def get_store(settings: Settings = Depends(get_settings)) -> OpenSearchStore:
    return OpenSearchStore(settings)


def get_ai(settings: Settings = Depends(get_settings)) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(settings)


@router.get("/health")
async def health(store: OpenSearchStore = Depends(get_store)) -> dict[str, str | bool]:
    return {"status": "ok", "opensearch": store.ping()}


@router.get("/state", response_model=AppStateResponse)
async def get_state() -> AppStateResponse:
    return AppStateResponse(**state)


@router.get("/config/public")
async def public_config(settings: Settings = Depends(get_settings)) -> dict[str, str | int]:
    return {
        "src_base_url": settings.src_base_url,
        "max_pages": settings.max_pages,
        "chunk_size": settings.chunk_size,
    }


@router.post("/index/rebuild", response_model=TaskCreateResponse)
async def start_rebuild(
    request: IndexRebuildRequest,
    settings: Settings = Depends(get_settings),
    store: OpenSearchStore = Depends(get_store),
    ai: OpenAICompatibleClient = Depends(get_ai),
) -> TaskCreateResponse:
    if state["indexing_running"]:
        raise HTTPException(status_code=409, detail="Индексация уже запущена")

    state["indexing_running"] = True
    state["index_ready"] = False

    task_id = stream_registry.create()

    async def run() -> None:
        async with index_lock:
            service = IndexService(settings, store, ai, src_base_url=request.src_base_url, source_mode=request.source_mode)
            try:
                await stream_registry.push(task_id, "status", {"message": "Запуск индексации", "progress": 1})

                async def emit_status(message: str, progress: int | None = None) -> None:
                    payload: dict[str, str | int] = {"message": message}
                    if progress is not None:
                        payload["progress"] = progress
                    await stream_registry.push(task_id, "status", payload)

                await service.rebuild(emit_status)
                state["index_ready"] = True
                await stream_registry.push(task_id, "done", {"ok": True})
            except Exception as exc:
                await stream_registry.push(task_id, "error", {"detail": str(exc)})
            finally:
                state["indexing_running"] = False
                await stream_registry.mark_done(task_id)

    asyncio.create_task(run())
    return TaskCreateResponse(task_id=task_id)


@router.post("/index/rebuild/files", response_model=TaskCreateResponse)
async def start_rebuild_from_files(
    files: list[UploadFile] = File(...),
    settings: Settings = Depends(get_settings),
    store: OpenSearchStore = Depends(get_store),
    ai: OpenAICompatibleClient = Depends(get_ai),
) -> TaskCreateResponse:
    if state["indexing_running"]:
        raise HTTPException(status_code=409, detail="Индексация уже запущена")
    if not files:
        raise HTTPException(status_code=400, detail="Не выбраны файлы")

    upload_dir = Path("uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    for file in files:
        original_name = Path(file.filename or "uploaded_file").name
        lower_name = original_name.lower()
        ext = ".tar.gz" if lower_name.endswith(".tar.gz") else Path(original_name).suffix.lower()
        if ext not in allowed_extensions and ext not in archive_extensions:
            raise HTTPException(status_code=400, detail=f"Формат файла {original_name} не поддерживается")

        target = upload_dir / original_name
        content = await file.read()
        target.write_bytes(content)
        saved_paths.append(target)

    state["indexing_running"] = True
    state["index_ready"] = False
    task_id = stream_registry.create()

    async def run() -> None:
        async with index_lock:
            service = IndexService(settings, store, ai)
            loader = ArchiveDocumentLoader()
            try:
                await stream_registry.push(task_id, "status", {"message": "Подготовка загруженных файлов", "progress": 10})
                crawled_docs = []
                for p in saved_paths:
                    for d in loader.load_path(p):
                        crawled_docs.append(CrawledDocument(url=d.url, title=d.title, text=d.text, source_type=d.source_type))
                if not crawled_docs:
                    raise RuntimeError("Не удалось извлечь текст из загруженных файлов")

                async def emit_status(message: str, progress: int | None = None) -> None:
                    payload: dict[str, str | int] = {"message": message}
                    if progress is not None:
                        payload["progress"] = progress
                    await stream_registry.push(task_id, "status", payload)

                await service.rebuild(emit_status, preloaded_docs=crawled_docs)
                state["index_ready"] = True
                await stream_registry.push(task_id, "done", {"ok": True})
            except Exception as exc:
                await stream_registry.push(task_id, "error", {"detail": str(exc)})
            finally:
                state["indexing_running"] = False
                await stream_registry.mark_done(task_id)

    asyncio.create_task(run())
    return TaskCreateResponse(task_id=task_id)


@router.get("/index/stream")
async def index_stream(task_id: str):
    task = stream_registry.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    async def event_generator():
        while True:
            try:
                payload = await asyncio.wait_for(task.queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if task.done and payload == "":
                break
            if payload:
                yield payload

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/search", response_model=TaskCreateResponse)
async def start_search(
    request: SearchRequest,
    settings: Settings = Depends(get_settings),
    store: OpenSearchStore = Depends(get_store),
    ai: OpenAICompatibleClient = Depends(get_ai),
) -> TaskCreateResponse:
    if state["indexing_running"]:
        raise HTTPException(status_code=409, detail="Поиск недоступен во время индексации")
    if not state["index_ready"] and not store.index_exists():
        raise HTTPException(status_code=409, detail="Индекс не готов")

    task_id = stream_registry.create()

    async def run() -> None:
        async with search_lock:
            search_service = SearchService(settings, store, ai)
            try:
                await search_service.run(request.query, lambda ev, data: stream_registry.push(task_id, ev, data))
                await stream_registry.push(task_id, "done", {"ok": True})
            except Exception as exc:
                await stream_registry.push(task_id, "error", {"detail": str(exc)})
            finally:
                await stream_registry.mark_done(task_id)

    asyncio.create_task(run())
    return TaskCreateResponse(task_id=task_id)


@router.get("/search/stream")
async def search_stream(task_id: str):
    task = stream_registry.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    async def event_generator():
        while True:
            try:
                payload = await asyncio.wait_for(task.queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if task.done and payload == "":
                break
            if payload:
                yield payload

    return StreamingResponse(event_generator(), media_type="text/event-stream")
