from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import fitz
import httpx


def filename_from_content_disposition(content_disposition: str) -> str | None:
    if not content_disposition:
        return None
    match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, flags=re.I)
    if match:
        return unquote(match.group(1)).strip('"')
    match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.I)
    if match:
        return unquote(match.group(1)).strip('"')
    return None


def filename_from_url(url: str, default_name: str = "downloaded_file") -> str:
    name = Path(unquote(urlparse(url).path)).name
    return name if name and "." in name else default_name


@dataclass
class RawDocument:
    url: str
    title: str
    text: str
    source_type: str


class ArchiveDocumentLoader:
    def load_path(self, path: str | Path) -> list[RawDocument]:
        p = Path(path)
        if p.is_dir():
            docs: list[RawDocument] = []
            for child in p.rglob("*"):
                if child.is_file():
                    docs.extend(self.load_path(child))
            return docs
        if not p.exists() or not p.is_file():
            return []

        suffix = p.suffix.lower()
        text = ""
        if suffix == ".pdf":
            with fitz.open(p) as doc:
                text = "\n".join(page.get_text("text") for page in doc)
        elif suffix in {".txt", ".md", ".html", ".htm", ".json", ".csv"}:
            raw = p.read_text(encoding="utf-8", errors="ignore")
            if suffix == ".json":
                try:
                    text = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
                except Exception:
                    text = raw
            elif suffix == ".csv":
                rows = list(csv.reader(io.StringIO(raw)))
                text = "\n".join(" | ".join(r) for r in rows[:300])
            else:
                text = raw

        cleaned = text.strip()
        if len(cleaned) < 30:
            return []

        return [RawDocument(url=f"file://{p.resolve()}", title=p.name, text=cleaned, source_type=suffix.lstrip("."))]


class URLDocumentDownloader:
    def __init__(self, download_dir: str | Path = "downloads"):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str) -> Path:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "").lower().split(";")[0].strip()
        name = filename_from_content_disposition(resp.headers.get("Content-Disposition", "")) or filename_from_url(str(resp.url))
        if "." not in Path(name).name:
            name = f"{name}{mimetypes.guess_extension(content_type) or ''}"

        out = self.download_dir / name
        out.write_bytes(resp.content)
        return out

    async def download_and_load_docs(self, url: str, loader: ArchiveDocumentLoader) -> list[RawDocument]:
        path = await self.download(url)
        return loader.load_path(path)
