from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    openai_base_url: str = Field(alias="OPENAI_BASE_URL")
    src_base_url: str = Field(alias="SRC_BASE_URL")
    embed_model_name: str = Field(alias="EMBED_MODEL_NAME")
    rerank_model_name: str = Field(alias="RERANK_MODEL_NAME")
    llm_model_name: str = Field(alias="LLM_MODEL_NAME")

    vector_storage_host: str = Field(alias="VECTOR_STORAGE_HOST")
    vector_storage_port: int = Field(alias="VECTOR_STORAGE_PORT")
    vector_storage_index: str = Field(alias="VECTOR_STORAGE_INDEX")
    vector_storage_username: str = Field(alias="VECTOR_STORAGE_USERNAME")
    vector_storage_password: str = Field(alias="VECTOR_STORAGE_PASSWORD")

    max_pages: int = Field(alias="MAX_PAGES")
    page_wait_ms: int = Field(alias="PAGE_WAIT_MS")
    chunk_size: int = Field(alias="CHUNK_SIZE")
    chunk_overlap: int = Field(alias="CHUNK_OVERLAP")
    default_retrieve_k: int = Field(alias="DEFAULT_RETRIEVE_K")
    default_rerank_k: int = Field(alias="DEFAULT_RERANK_K")
    max_context_chars: int = Field(alias="MAX_CONTEXT_CHARS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
