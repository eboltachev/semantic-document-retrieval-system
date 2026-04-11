import hashlib


def make_chunk_id(url: str, chunk_index: int, text: str) -> str:
    payload = f"{url}|{chunk_index}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]
