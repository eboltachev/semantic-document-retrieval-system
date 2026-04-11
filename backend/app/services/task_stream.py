from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field


@dataclass
class StreamTask:
    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    done: bool = False


class StreamRegistry:
    def __init__(self) -> None:
        self.tasks: dict[str, StreamTask] = {}

    def create(self) -> str:
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = StreamTask()
        return task_id

    async def push(self, task_id: str, event: str, data: dict) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        task = self.tasks.get(task_id)
        if task:
            await task.queue.put(payload)

    async def mark_done(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task:
            task.done = True
            await task.queue.put("")

    def get(self, task_id: str) -> StreamTask | None:
        return self.tasks.get(task_id)
