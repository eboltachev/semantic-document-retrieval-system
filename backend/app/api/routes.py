from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.schemas.indexing import AppStateResponse, TaskCreateResponse
from app.schemas.search import SearchRequest
from app.services.ai_client import OpenAICompatibleClient
from app.services.indexer import IndexService
from app.services.opensearch_store import OpenSearchStore
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
            service = IndexService(settings, store, ai)
            try:
                await stream_registry.push(task_id, "status", {"message": "Запуск индексации"})
                await service.rebuild(lambda msg: stream_registry.push(task_id, "status", {"message": msg}))
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
            payload = await task.queue.get()
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
            payload = await task.queue.get()
            if task.done and payload == "":
                break
            if payload:
                yield payload

    return StreamingResponse(event_generator(), media_type="text/event-stream")
