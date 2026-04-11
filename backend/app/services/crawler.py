from __future__ import annotations

import io
from collections import deque
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

import fitz
import httpx
from playwright.async_api import Browser, async_playwright

from app.core.config import Settings
from app.utils.text import clean_text


@dataclass
class CrawledDocument:
    url: str
    title: str
    text: str
    source_type: str
    parent_url: str | None = None


class SiteCrawler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = settings.src_base_url
        self.base_host = urlparse(self.base).netloc

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        cleaned = parsed._replace(fragment="", query=parsed.query)
        normalized = urlunparse(cleaned)
        if normalized.endswith("/") and len(normalized) > len(f"{parsed.scheme}://{parsed.netloc}/"):
            normalized = normalized[:-1]
        return normalized

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.base_host

    async def _extract_pdf(self, client: httpx.AsyncClient, url: str, parent: str | None) -> CrawledDocument | None:
        response = await client.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        doc = fitz.open(stream=io.BytesIO(response.content), filetype="pdf")
        text = "\n".join(page.get_text("text") for page in doc)
        cleaned = clean_text(text, max_len=120_000)
        if not cleaned:
            return None
        title = url.split("/")[-1] or "PDF"
        return CrawledDocument(url=url, title=title, text=cleaned, source_type="pdf", parent_url=parent)

    async def _extract_html(self, browser: Browser, url: str, parent: str | None) -> tuple[CrawledDocument | None, list[str]]:
        page = await browser.new_page()
        links: list[str] = []
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(self.settings.page_wait_ms)
            title = await page.title()
            text = await page.evaluate(
                """
                () => {
                  const selectors = ['main', 'article', '.content', '.markdown-body', 'body'];
                  for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.trim().length > 0) {
                      return el.innerText;
                    }
                  }
                  return document.body ? document.body.innerText : '';
                }
                """
            )
            if not title:
                h1 = await page.query_selector("h1")
                title = (await h1.inner_text()) if h1 else url
            raw_links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
            for href in raw_links:
                if not href:
                    continue
                joined = urljoin(url, href)
                normalized = self._normalize_url(joined)
                if self._is_same_domain(normalized):
                    links.append(normalized)
            cleaned = clean_text(text, max_len=120_000)
            if not cleaned:
                return None, links
            return CrawledDocument(url=url, title=clean_text(title, 300), text=cleaned, source_type="html", parent_url=parent), links
        finally:
            await page.close()

    async def crawl(self, status_cb) -> list[CrawledDocument]:
        visited: set[str] = set()
        queue: deque[tuple[str, str | None]] = deque([(self._normalize_url(self.base), None)])
        collected: list[CrawledDocument] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            async with httpx.AsyncClient() as client:
                while queue and len(collected) < self.settings.max_pages:
                    url, parent = queue.popleft()
                    if url in visited:
                        continue
                    visited.add(url)
                    await status_cb(f"Краулинг: {url}")
                    try:
                        if url.lower().endswith(".pdf"):
                            pdf_doc = await self._extract_pdf(client, url, parent)
                            if pdf_doc:
                                collected.append(pdf_doc)
                            continue

                        doc, links = await self._extract_html(browser, url, parent)
                        if doc:
                            collected.append(doc)
                        for link in links:
                            if link not in visited:
                                queue.append((link, url))
                    except Exception:
                        await status_cb(f"Пропуск страницы из-за ошибки: {url}")
                        continue
            await browser.close()
        return collected
