from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from opensearchpy import OpenSearch, helpers

from app.core.config import Settings


class OpenSearchStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OpenSearch(
            hosts=[{"host": settings.vector_storage_host, "port": settings.vector_storage_port}],
            http_auth=(settings.vector_storage_username, settings.vector_storage_password),
            use_ssl=False,
            verify_certs=False,
            ssl_assert_hostname=False,
            ssl_show_warn=False,
        )

    def ping(self) -> bool:
        return bool(self.client.ping())

    def index_exists(self) -> bool:
        return bool(self.client.indices.exists(index=self.settings.vector_storage_index))

    def delete_index(self) -> None:
        if self.index_exists():
            self.client.indices.delete(index=self.settings.vector_storage_index)

    def create_index(self, embedding_dims: int) -> None:
        body = {
            "settings": {
                "index": {"knn": True},
                "analysis": {"analyzer": {"default": {"type": "standard"}}},
            },
            "mappings": {
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "url": {"type": "keyword"},
                    "title": {"type": "text"},
                    "source_type": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                    "text": {"type": "text"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": embedding_dims,
                        "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "nmslib"},
                    },
                }
            },
        }
        self.client.indices.create(index=self.settings.vector_storage_index, body=body)

    def bulk_upsert(self, docs: Iterable[dict[str, Any]]) -> None:
        actions = [
            {
                "_index": self.settings.vector_storage_index,
                "_id": d["chunk_id"],
                "_source": d,
            }
            for d in docs
        ]
        if actions:
            helpers.bulk(self.client, actions)

    def vector_search(self, embedding: list[float], k: int) -> list[dict[str, Any]]:
        body = {
            "size": k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": embedding,
                        "k": k,
                    }
                }
            },
        }
        resp = self.client.search(index=self.settings.vector_storage_index, body=body)
        return resp.get("hits", {}).get("hits", [])

    def keyword_search(self, query: str, k: int) -> list[dict[str, Any]]:
        body = {
            "size": k,
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["title^2", "text"],
                    "type": "best_fields",
                }
            },
        }
        resp = self.client.search(index=self.settings.vector_storage_index, body=body)
        return resp.get("hits", {}).get("hits", [])
