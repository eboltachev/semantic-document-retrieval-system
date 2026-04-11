from pydantic import BaseModel


class TaskCreateResponse(BaseModel):
    task_id: str


class IndexRebuildRequest(BaseModel):
    src_base_url: str | None = None


class AppStateResponse(BaseModel):
    indexing_running: bool
    index_ready: bool
