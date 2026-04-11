from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


class SourceItem(BaseModel):
    title: str
    url: str
    preview: str
