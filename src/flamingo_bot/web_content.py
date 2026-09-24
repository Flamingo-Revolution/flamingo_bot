"""Small, bounded readers for public editorial HTML used by ingestion."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

_NEWS_PATH = re.compile(r"^/news/[^/]+/$")
_BLOCK_TAGS = {"article", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "li", "p", "section"}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fetch_public_html(url: str) -> str:
    """Fetch a public HTML page with a deadline and a bounded response body."""
    request = Request(url, headers={"User-Agent": "FlamingoBotKnowledgeIngest/1.0"})
    with urlopen(request, timeout=20) as response:
        content_type = response.headers.get_content_type()
        if content_type != "text/html":
            raise ValueError(f"Expected HTML at {url}; received {content_type}")
        payload: bytes = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise ValueError(f"HTML page exceeds 2 MB: {url}")
        return payload.decode(response.headers.get_content_charset() or "utf-8", errors="replace")


class _NewsLinks(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        parsed = urlparse(urljoin(self.base_url, href))
        if parsed.netloc == urlparse(self.base_url).netloc and _NEWS_PATH.fullmatch(parsed.path):
            self.urls.add(parsed._replace(query="", fragment="").geturl())


def discover_news_urls(index_html: str, base_url: str) -> list[str]:
    parser = _NewsLinks(base_url)
    parser.feed(index_html)
    return sorted(parser.urls)


class _MainText(HTMLParser):
    def __init__(self, required_class: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.required_class = required_class
        self.depth = 0
        self.skip_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if not self.depth:
            classes = (attributes.get("class") or "").split()
            if tag == "main" and (self.required_class is None or self.required_class in classes):
                self.depth = 1
            return
        if self.skip_depth:
            if tag not in _VOID_TAGS:
                self.skip_depth += 1
                self.depth += 1
            return
        if tag in {"nav", "script", "style", "noscript", "svg", "button", "form"}:
            self.skip_depth = 1
        if tag == "h1":
            self.in_h1 = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n\n")
        if tag not in _VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self.depth or tag in _VOID_TAGS:
            return
        if self.skip_depth:
            self.skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")
        if tag == "h1":
            self.in_h1 = False
        self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and not self.skip_depth:
            self.parts.append(data)
            if self.in_h1:
                self.title_parts.append(data)


def extract_main_text(page_html: str, *, required_class: str | None = None) -> tuple[str, str]:
    parser = _MainText(required_class)
    parser.feed(page_html)
    text = _normalize_text("".join(parser.parts))
    title = _normalize_text(" ".join(parser.title_parts)) or next(
        (line for line in text.splitlines() if line.strip()), ""
    )
    title = " ".join(title.split())
    if not title or len(text) < 80:
        raise ValueError("Public page has no substantial main article text")
    return title, text
