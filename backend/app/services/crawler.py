from __future__ import annotations

import io
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

import fitz
import httpx
from playwright.async_api import Page, async_playwright

from app.core.config import Settings
from app.utils.text import clean_text

PDF_EXTENSIONS = {".pdf"}


@dataclass
class CrawledDocument:
    url: str
    title: str
    text: str
    source_type: str
    parent_url: str | None = None


class SiteCrawler:
    def __init__(self, settings: Settings, src_base_url: str | None = None):
        self.settings = settings
        self.base_url = self._normalize_url(src_base_url or settings.src_base_url)
        self.base_host = urlparse(self.base_url).netloc

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url.strip())
        cleaned = parsed._replace(fragment="")
        normalized = urlunparse(cleaned)
        if normalized.endswith("/") and len(normalized) > len(f"{parsed.scheme}://{parsed.netloc}/"):
            normalized = normalized[:-1]
        return normalized

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.base_host

    def _get_extension(self, url: str) -> str:
        path = urlparse(url).path.lower()
        return path[path.rfind(".") :] if "." in path else ""

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str:
        text_parts: list[str] = []
        with fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf") as doc:
            for page in doc:
                text_parts.append(page.get_text("text"))
        return clean_text("\n".join(text_parts), max_len=120_000)

    async def _try_download_pdf(self, client: httpx.AsyncClient, url: str) -> CrawledDocument | None:
        try:
            resp = await client.get(url, timeout=20.0, follow_redirects=True)
            if resp.status_code != 200:
                return None
            if "application/pdf" not in resp.headers.get("content-type", "").lower():
                return None
            text = self._extract_pdf_text(resp.content)
            if not text:
                return None
            title = urlparse(url).path.rsplit("/", 1)[-1] or "PDF document"
            return CrawledDocument(url=url, title=title, text=text, source_type="pdf")
        except Exception:
            return None

    async def _extract_links_from_page(self, page: Page) -> list[str]:
        hrefs = await page.eval_on_selector_all("a[href]", "(els) => els.map(a => a.href).filter(Boolean)")
        result: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            normalized = self._normalize_url(href)
            if self._is_same_domain(normalized) and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    async def _get_rendered_title(self, page: Page) -> str:
        try:
            title = await page.title()
            if title and title.strip():
                return clean_text(title, 300)
        except Exception:
            pass
        try:
            h1 = page.locator("h1").first
            if await h1.count() > 0:
                text = await h1.text_content(timeout=2000)
                if text and text.strip():
                    return clean_text(text, 300)
        except Exception:
            pass
        return "Document"

    async def _get_rendered_text(self, page: Page) -> str:
        selectors = [
            "main",
            "article",
            ".content",
            ".markdown-section",
            "section.content",
            "body",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    text = await locator.first.inner_text(timeout=2000)
                    cleaned = clean_text(text, 120_000)
                    if len(cleaned) > 80:
                        return cleaned
            except Exception:
                continue
        return ""

    async def _click_docs_button_if_present(self, page: Page) -> None:
        for text in ["Ознакомиться с документацией", "Documentation", "Docs"]:
            try:
                locator = page.get_by_text(text, exact=False)
                if await locator.count() > 0:
                    await locator.first.click(timeout=3000)
                    await page.wait_for_timeout(self.settings.page_wait_ms)
                    return
            except Exception:
                continue

    async def crawl(self, status_cb) -> list[CrawledDocument]:
        docs: list[CrawledDocument] = []
        visited: set[str] = set()
        queue: list[str] = []

        await status_cb("Краулинг документов")

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = await browser.new_context(viewport={"width": 1440, "height": 2200}, ignore_https_errors=True)
            page = await context.new_page()

            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(self.settings.page_wait_ms)
            await self._click_docs_button_if_present(page)

            current_url = self._normalize_url(page.url)
            try:
                first_links = await self._extract_links_from_page(page)
            except Exception:
                first_links = []

            queue = [current_url, *first_links]

            async with httpx.AsyncClient() as client:
                while queue and len(visited) < self.settings.max_pages:
                    url = self._normalize_url(queue.pop(0))
                    if url in visited or not self._is_same_domain(url):
                        continue
                    visited.add(url)

                    if self._get_extension(url) in PDF_EXTENSIONS:
                        pdf_doc = await self._try_download_pdf(client, url)
                        if pdf_doc:
                            docs.append(pdf_doc)
                        continue

                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                        await page.wait_for_timeout(self.settings.page_wait_ms)
                    except Exception:
                        continue

                    title = await self._get_rendered_title(page)
                    text = await self._get_rendered_text(page)
                    if text and len(text) > 30:
                        docs.append(CrawledDocument(url=url, title=title, text=text, source_type="html"))

                    try:
                        new_links = await self._extract_links_from_page(page)
                    except Exception:
                        new_links = []
                    for link in new_links:
                        if link not in visited and link not in queue:
                            queue.append(link)

            await context.close()
            await browser.close()

        return docs
