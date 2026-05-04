from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
import gzip
import xml.etree.ElementTree as ET

import fitz
import httpx

from app.core.config import allowed_extensions, archive_extensions


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
    def _xml_to_text(self, content: bytes) -> str:
        try:
            root = ET.fromstring(content)
            return "\n".join(part.strip() for part in root.itertext() if part and part.strip())
        except Exception:
            return ""

    def _read_docx(self, p: Path) -> str:
        with zipfile.ZipFile(p) as zf:
            return self._xml_to_text(zf.read("word/document.xml")) if "word/document.xml" in zf.namelist() else ""

    def _read_pptx(self, p: Path) -> str:
        texts: list[str] = []
        with zipfile.ZipFile(p) as zf:
            for name in sorted(n for n in zf.namelist() if n.startswith("ppt/slides/") and n.endswith(".xml")):
                texts.append(self._xml_to_text(zf.read(name)))
        return "\n".join(t for t in texts if t)

    def _read_xlsx(self, p: Path) -> str:
        texts: list[str] = []
        with zipfile.ZipFile(p) as zf:
            for name in zf.namelist():
                if name.startswith("xl/") and name.endswith(".xml"):
                    texts.append(self._xml_to_text(zf.read(name)))
        return "\n".join(t for t in texts if t)

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

        name_lower = p.name.lower()
        suffix = p.suffix.lower()

        if name_lower.endswith(".tar.gz"):
            suffix = ".tar.gz"

        if suffix in archive_extensions:
            docs: list[RawDocument] = []
            with tempfile.TemporaryDirectory() as td:
                tmp_dir = Path(td)
                try:
                    if suffix == ".zip":
                        with zipfile.ZipFile(p) as zf:
                            zf.extractall(tmp_dir)
                    elif suffix in {".tar", ".tgz", ".tar.gz"}:
                        with tarfile.open(p, "r:*") as tf:
                            tf.extractall(tmp_dir)
                    elif suffix == ".gz":
                        out_name = p.stem or "unzipped"
                        out_path = tmp_dir / out_name
                        with gzip.open(p, "rb") as src, out_path.open("wb") as dst:
                            dst.write(src.read())
                    else:
                        return []
                except Exception:
                    return []
                for child in tmp_dir.rglob("*"):
                    if child.is_file():
                        docs.extend(self.load_path(child))
            return docs

        if suffix not in allowed_extensions:
            return []

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
        elif suffix == ".docx":
            text = self._read_docx(p)
        elif suffix == ".pptx":
            text = self._read_pptx(p)
        elif suffix == ".xlsx":
            text = self._read_xlsx(p)
        elif suffix == ".rtf":
            raw = p.read_text(encoding="utf-8", errors="ignore")
            text = re.sub(r"\\[a-zA-Z]+-?\d* ?|[{}]", " ", raw)
        elif suffix == ".doc":
            text = p.read_text(encoding="utf-8", errors="ignore")

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
