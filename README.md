# Semantic Document Retrieval System

Сервис семантического поиска по документации `https://docs.pro-online.ru/` с переиндексацией, гибридным retrieval, реранжированием и потоковой SSE-выдачей статусов/ответа.

## Быстрый старт

```bash
cp .env.example .env
docker compose up -d --build
```

Откройте `http://localhost:${APP_PORT}`.

## Проверка после запуска

```bash
curl http://localhost:${APP_PORT}/api/health
```

> `127.0.0.1:8000` не открыт наружу намеренно: backend доступен только во внутренней docker-сети.
> Внешняя точка входа только одна — `http://localhost:${APP_PORT}`.

## Архитектура

- `backend` — FastAPI + Playwright crawler + PyMuPDF + OpenSearch + OpenAI-compatible API.
- `frontend` — React (Vite build) + Nginx (статический UI + reverse proxy к backend).

## API

- `GET /api/health`
- `GET /api/state`
- `POST /api/index/rebuild`
- `GET /api/index/stream?task_id=...`
- `POST /api/search`
- `GET /api/search/stream?task_id=...`
- `GET /api/config/public`

## Потоки SSE

События индексации: `status`, `done`, `error`.

События поиска: `status`, `token`, `sources`, `done`, `error`.

## Примечания

- Повторная индексация полностью удаляет индекс и создаёт заново.
- Поиск блокируется, пока индекс не готов.
- Источники формируются только из реально использованных чанков.
