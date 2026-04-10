from pydantic import BaseModel


class TaskCreateResponse(BaseModel):
    task_id: str


class AppStateResponse(BaseModel):
    indexing_running: bool
    index_ready: bool
