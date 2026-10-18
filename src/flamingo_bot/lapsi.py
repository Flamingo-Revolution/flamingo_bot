"""Bounded, incremental collection of protest-related Lapsi article references.

Only publisher-provided metadata is retained. This module never sends article
bodies to an AI model and does not treat crawl signals as a copyright licence.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BASE_URL = "https://lapsi.al"
ROBOTS_URL = BASE_URL + "/robots.txt"
SITEMAP_URL = BASE_URL + "/sitemap.xml"
USER_AGENT = "FlamingoBotProtestReferences/1.0 (+https://www.flamingorevolution.eu/)"
PROTEST_START = datetime(2026, 5, 31, tzinfo=ZoneInfo("Europe/Tirane"))
FILTER_VERSION = "protest-topics/1"
MAX_RESPONSE_BYTES = 5_000_000
MAX_EXCERPT_CHARS = 280
_ARTICLE_PATH = re.compile(r"/(?:[^/?#\s]+|\d{4}/\d{2}/\d{2}/[^/?#\s]+)/*$")
_MONTH_PATH = re.compile(r"/sitemaps/sitemap-(\d{4})-(\d{2})\.xml$")
_CANDIDATE = re.compile(
    r"flaming|protest|revolt|marshim|student|zvernec|sazan|rrjoll|renea|"
    r"plumba-gome|gaz-lotsjelles|dhun.{0,12}polic|polic.{0,12}dhun",
    re.I,
)


def normalize_url(url: str, *, article: bool = False) -> str:
    """Allow only public Lapsi HTTPS URLs; remove tracking and fragments."""
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in {"lapsi.al", "www.lapsi.al"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or "\\" in url
        or any(ord(character) < 32 for character in url)
        or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
    ):
        raise ValueError("Only public HTTPS URLs on lapsi.al are allowed")
    path = parsed.path or "/"
    if article and (
        not _ARTICLE_PATH.fullmatch(path)
        or any(part.startswith(".") for part in unquote(path).split("/") if part)
    ):
        raise ValueError("Expected a Lapsi article URL, not a listing or another resource")
    return BASE_URL + path.rstrip("/") if path != "/" else BASE_URL + "/"


def article_key(url: str) -> str:
    path = urlparse(normalize_url(url, article=True)).path
    match = re.search(r"-i(\d+)$", path)
    return match.group(1) if match else "legacy-" + hashlib.sha256(path.encode()).hexdigest()[:24]


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(character for character in value if not unicodedata.combining(character)).split()
    )


def _clean(value: str) -> str:
    return " ".join(re.sub(r"<[^>]*>", " ", html.unescape(value)).split())


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Article and checkpoint timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("A scan time must include a timezone")
    return value.astimezone(UTC).isoformat()


def protest_topics(title: str, excerpt: str, section: str = "") -> tuple[int, list[str]]:
    """A topical relevance rule, not a popularity or political-sentiment score."""
    text = _fold(title + " " + excerpt)
    if _fold(section) in {"sport", "sports", "paparazzi", "stil", "planet"}:
        return 0, []
    if re.search(r"\bflaming\w*\b|diaspora zbarkon", text):
        return 100, ["flamingo-movement"]
    action = bool(re.search(r"\b(?:protest|revolt|marshim|demonstrat)\w*\b", text))
    local = bool(
        re.search(
            r"shqip\w*|tirane?\w*|kryeministr\w*|\brama\b|ministris\w*|"
            r"skenderbej|zvernec|sazan|rrjoll|vlore|durres|shkoder|korce|elbasan",
            text,
        )
    )
    foreign = bool(
        re.search(
            r"gaza|palestin|izrael|prishtin|kosov|serbi|beograd|franc\w*|paris|"
            r"amerik\w*|shba|trump|londer|london|nepal|indonezi|gjeorgji|\barbit\w*",
            text,
        )
    )
    movement_target = bool(
        re.search(
            r"shqiperi\w*|tirane?\w*|\brama\b|zvernec|sazan|rrjoll",
            text,
        )
    )
    if not local or (foreign and not movement_target):
        return 0, []
    if not action:
        return 0, []
    if re.search(r"\bberisha\b|partia demokratike|\bpd\b", text) and not re.search(
        r"student\w*|qytetar\w*|revolt\w*|zvernec|sazan|rrjoll",
        text,
    ):
        return 0, []
    topics = ["protest-events"]
    if re.search(r"student\w*", text):
        topics.append("student-protests")
    if re.search(r"polic\w*|renea|gaz lotsjelles|plumba gome|arrest\w*|ndalim\w*", text):
        topics.append("police-response")
    if re.search(r"zvernec|sazan|rrjoll|zon\w* (?:e )?mbrojt\w*", text):
        topics.append("protest-causes")
    return 40 + 10 * (len(topics) - 1), topics


class ArticleReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    aliases: list[str] = Field(default_factory=list, max_length=10)
    title: str = Field(min_length=5, max_length=500)
    publisher_excerpt: str = Field(min_length=20, max_length=MAX_EXCERPT_CHARS)
    author: str = Field(default="Lapsi.al", max_length=200)
    section: str = Field(default="", max_length=100)
    published_at: str
    modified_at: str | None = None
    captured_at: str
    topics: list[str] = Field(min_length=1, max_length=6)
    relevance_score: int = Field(ge=1, le=100)
    content_use: Literal["reference"] = "reference"
    excerpt_kind: Literal["publisher-description"] = "publisher-description"
    policy_url: Literal["https://lapsi.al/robots.txt"] = "https://lapsi.al/robots.txt"

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return normalize_url(value, article=True)

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, values: list[str]) -> list[str]:
        return sorted({normalize_url(value, article=True) for value in values})

    @field_validator("published_at", "modified_at", "captured_at")
    @classmethod
    def valid_timestamp(cls, value: str | None) -> str | None:
        return _iso(_timestamp(value)) if value is not None else None

    @property
    def key(self) -> str:
        return article_key(self.url)

    @property
    def revision(self) -> str:
        data = self.model_dump(exclude={"captured_at"})
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()


class ReferenceIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["lapsi-reference/1"] = "lapsi-reference/1"
    articles: list[ArticleReference] = Field(default_factory=list, max_length=20_000)

    @model_validator(mode="after")
    def distinct_articles(self) -> ReferenceIndex:
        if len({article.key for article in self.articles}) != len(self.articles):
            raise ValueError("The reference index contains duplicate article identities")
        return self


def _current_reference(article: ArticleReference) -> ArticleReference | None:
    score, topics = protest_topics(article.title, article.publisher_excerpt, article.section)
    if not score:
        return None
    return article.model_copy(update={"relevance_score": score, "topics": topics})


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lastmod: str | None = None
    checked_at: str

    @field_validator("lastmod", "checked_at")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        return _iso(_timestamp(value)) if value is not None else None


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["lapsi-checkpoint/1"] = "lapsi-checkpoint/1"
    protest_start: Literal["2026-05-31"] = "2026-05-31"
    filter_version: str = FILTER_VERSION
    last_successful_scan: str | None = None
    observations: dict[str, Observation] = Field(default_factory=dict)

    @field_validator("last_successful_scan")
    @classmethod
    def valid_checkpoint(cls, value: str | None) -> str | None:
        return _iso(_timestamp(value)) if value is not None else None


class CachedMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: Observation
    reference: ArticleReference | None


class CollectionCache(BaseModel):
    """Resumable work, never an active index or a successful-scan checkpoint."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["lapsi-collection-cache/1"] = "lapsi-collection-cache/1"
    filter_version: str = FILTER_VERSION
    entries: dict[str, CachedMetadata] = Field(default_factory=dict)


class _Metadata(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.finished = False
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "head" and not self.finished:
            self.in_head = True
        if not self.in_head:
            return
        attributes = dict(attrs)
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name") or attributes.get("itemprop")
            value = attributes.get("content")
            if key and value and key not in self.values:
                self.values[key] = _clean(value)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").split():
            self.values["canonical"] = attributes.get("href") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head = False
            self.finished = True


def extract_reference(page: str, url: str, *, captured_at: datetime) -> ArticleReference | None:
    parser = _Metadata()
    parser.feed(page)
    fields = parser.values
    title = fields.get("og:title") or fields.get("headline") or ""
    excerpt = fields.get("og:description") or fields.get("description") or ""
    published = fields.get("article:published_time") or fields.get("datePublished")
    if not title or not published:
        raise ValueError(f"Article has no reliable title or publication timestamp: {url}")
    published_at = _timestamp(published)
    if not PROTEST_START <= published_at <= captured_at:
        return None
    section = fields.get("article:section", "")
    score, topics = protest_topics(title, excerpt, section)
    if not score or len(excerpt) < 20:
        return None
    if len(excerpt) > MAX_EXCERPT_CHARS:
        excerpt = excerpt[: MAX_EXCERPT_CHARS - 1].rsplit(" ", 1)[0] + "…"
    canonical = normalize_url(fields.get("canonical") or fields.get("og:url") or url, article=True)
    modified = fields.get("article:modified_time") or fields.get("dateModified")
    return ArticleReference(
        url=canonical,
        aliases=sorted({normalize_url(url, article=True), canonical}),
        title=title,
        publisher_excerpt=excerpt,
        author=fields.get("author") or "Lapsi.al",
        section=section,
        published_at=_iso(published_at),
        modified_at=_iso(_timestamp(modified)) if modified else None,
        captured_at=_iso(captured_at),
        topics=topics,
        relevance_score=score,
    )


class RobotsPolicy:
    def __init__(self, text: str) -> None:
        self.parser = RobotFileParser(ROBOTS_URL)
        self.parser.parse(text.splitlines())
        self.signals: dict[str, str] = {}
        raw_rules: list[tuple[list[str], str, bool]] = []
        agents: list[str] = []
        has_directive = False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            if key.casefold() == "user-agent":
                if has_directive:
                    agents = []
                    has_directive = False
                agents.append(value.casefold())
                continue
            has_directive = True
            applies = any(agent == "*" or agent in USER_AGENT.casefold() for agent in agents)
            if key.casefold() in {"allow", "disallow"} and applies:
                raw_rules.append((list(agents), value, key.casefold() == "allow" or not value))
            if key.casefold() == "content-signal" and applies:
                for signal in value.split(","):
                    name, separator, setting = signal.strip().partition("=")
                    if separator:
                        name, setting = name.casefold().strip(), setting.casefold().strip()
                        previous = self.signals.get(name)
                        if name == "use":
                            levels = {"immediate": 0, "reference": 1, "full": 2}
                            if setting not in levels:
                                raise ValueError(
                                    "Unknown content-use signal; review the site's policy"
                                )
                            if previous is None or levels[setting] < levels[previous]:
                                self.signals[name] = setting
                        elif previous != "no":
                            self.signals[name] = setting
        if self.signals.get("search") == "no" or self.signals.get("ai-input") == "no":
            raise ValueError("The site's current content signals disallow this reference index")
        if self.signals.get("use") == "immediate":
            raise ValueError("The site's current policy disallows retaining an article index")
        specificities = [
            max(
                len(agent) if agent != "*" else 0
                for agent in group
                if agent == "*" or agent in USER_AGENT.casefold()
            )
            for group, _, _ in raw_rules
        ]
        most_specific = max(specificities, default=0)
        self.rules = []
        for (_, pattern, allow), specificity in zip(raw_rules, specificities, strict=True):
            if specificity != most_specific:
                continue
            anchored = pattern.endswith("$")
            pattern = pattern.removesuffix("$")
            expression = "^" + re.escape(pattern).replace(r"\*", ".*")
            if anchored:
                expression += "$"
            length = len(pattern.replace("*", "").encode())
            self.rules.append((length, allow, re.compile(expression)))

    def check(self, url: str) -> None:
        parsed = urlparse(url)
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        matches = [
            (length, allow) for length, allow, pattern in self.rules if pattern.search(target)
        ]
        # RFC 9309: the longest path wins, with Allow winning equal-length ties.
        if matches and not max(matches)[1]:
            raise ValueError(f"robots.txt disallows this crawler at {url}")

    @property
    def delay(self) -> float:
        delay = float(self.parser.crawl_delay(USER_AGENT) or 0)
        rate = self.parser.request_rate(USER_AGENT)
        if rate is not None:
            if rate.requests < 1:
                raise ValueError("The robots request rate must allow at least one request")
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 60:
            raise ValueError("The crawl delay exceeds this script's 60-second wait limit")
        return delay


class _SafeRedirect(HTTPRedirectHandler):
    def __init__(self, check: Callable[[str], None]) -> None:
        self.check = check

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicReader:
    def __init__(self, *, delay: float = 0.5) -> None:
        if not 0.5 <= delay <= 60:
            raise ValueError("The request delay must be between 0.5 and 60 seconds")
        self.delay = delay
        self.policy: RobotsPolicy | None = None
        self.last_request = 0.0
        self.opener = build_opener(_SafeRedirect(self._before_request))

    def _check(self, url: str) -> None:
        normalize_url(url)
        if self.policy is not None:
            self.policy.check(url)

    def _before_request(self, url: str) -> None:
        self._check(url)
        delay = max(self.delay, self.policy.delay if self.policy else 0)
        wait = self.last_request + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()

    def __call__(self, url: str) -> str:
        self._before_request(url)
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self.opener.open(request, timeout=20) as response:
            self._check(response.geturl())
            kind = response.headers.get_content_type()
            allowed = {"text/html", "application/xml", "text/xml", "text/plain"}
            if kind not in allowed:
                raise ValueError(f"Unexpected response type for {url}: {kind}")
            data: bytes
            if kind == "text/html":
                # References need only publisher metadata. Stop at </head> and
                # close the stream rather than downloading a full article body.
                data = b""
                while len(data) <= 256_000:
                    piece: bytes = response.read(8192)
                    data += piece
                    end = re.search(rb"</head\s*>", data, flags=re.I)
                    if end:
                        data = data[: end.end()]
                        break
                    if not piece:
                        raise ValueError(f"HTML response has no complete metadata head: {url}")
                else:
                    raise ValueError(f"HTML metadata head exceeds 256 KB: {url}")
            else:
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ValueError(f"Public response exceeds 5 MB: {url}")
            return data.decode(response.headers.get_content_charset() or "utf-8", errors="strict")


def sitemap_entries(text: str, *, index: bool) -> list[tuple[str, datetime | None]]:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Sitemap document types and entities are not supported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Some Lapsi sitemaps contain an unescaped '&' in optional thumbnail
        # PageMap metadata. Discard only that extension, never core loc/lastmod.
        without_pagemap = re.sub(
            r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?PageMap)\b[^>]*>.*?</(?P=tag)\s*>",
            "",
            text,
            flags=re.S,
        )
        try:
            root = ET.fromstring(without_pagemap)
        except ET.ParseError as exc:
            raise ValueError("The sitemap XML is malformed; the scan is incomplete") from exc
    if root.tag.rsplit("}", 1)[-1] != ("sitemapindex" if index else "urlset"):
        raise ValueError("Expected a sitemap index" if index else "Expected an article sitemap")
    if len(root) > 50_000:
        raise ValueError("Sitemap exceeds 50,000 entries")
    result = []
    for entry in root:
        values = {child.tag.rsplit("}", 1)[-1]: child.text for child in entry}
        if not values.get("loc"):
            raise ValueError("Sitemap entry has no URL")
        raw_url = values["loc"] or ""
        url = normalize_url(raw_url)
        if not index:
            try:
                url = normalize_url(raw_url, article=True)
            except ValueError:
                if _CANDIDATE.search(unquote(urlparse(url).path)):
                    raise ValueError(f"Unsupported protest article URL in sitemap: {url}") from None
                # A few archive entries are malformed, unrelated legacy paths.
                # Do not fetch them or let them invalidate unrelated coverage.
                continue
        lastmod = values.get("lastmod")
        result.append((url, _timestamp(lastmod) if lastmod else None))
    return result


def read_reference_index(path: Path) -> ReferenceIndex:
    raw = path.read_bytes()
    if len(raw) > 16_000_000:
        raise ValueError("The Lapsi reference index exceeds 16 MB")
    return ReferenceIndex.model_validate_json(raw)


def _atomic_json(path: Path, data: BaseModel) -> None:
    import os

    temporary: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data.model_dump_json(indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _writer_lock(root: Path, *, dry_run: bool) -> Iterator[None]:
    if dry_run:
        yield
        return
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    with (root / ".snapshot.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another Lapsi snapshot writer is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_snapshot(
    output: Path,
    *,
    dry_run: bool,
    fetch: Callable[[str], str] | None = None,
    now: datetime | None = None,
    urls: Sequence[str] = (),
    full_rescan: bool = False,
    max_articles: int = 2000,
    max_sitemaps: int = 60,
    overlap_hours: int = 48,
    delay: float = 0.5,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Complete a bounded scan before changing either snapshots or the checkpoint."""
    if not 1 <= max_articles <= 5000 or not 1 <= max_sitemaps <= 240:
        raise ValueError("Article/sitemap limits must be within 1-5000 and 1-240")
    if not 1 <= overlap_hours <= 168:
        raise ValueError("The overlap must be between 1 and 168 hours")
    now = now or datetime.now(UTC)
    _iso(now)
    if now < PROTEST_START:
        raise ValueError("The scan end cannot precede 31 May 2026")
    manual_urls = sorted({normalize_url(url, article=True) for url in urls})
    with _writer_lock(output, dry_run=dry_run):
        return _scan(
            output,
            dry_run=dry_run,
            fetch=fetch or PublicReader(delay=delay),
            now=now,
            urls=manual_urls,
            full_rescan=full_rescan,
            max_articles=max_articles,
            max_sitemaps=max_sitemaps,
            overlap_hours=overlap_hours,
            progress=progress,
        )


def _scan(
    output: Path,
    *,
    dry_run: bool,
    fetch: Callable[[str], str],
    now: datetime,
    urls: list[str],
    full_rescan: bool,
    max_articles: int,
    max_sitemaps: int,
    overlap_hours: int,
    progress: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    index_path, checkpoint_path = output / "index.json", output / "checkpoint.json"
    previous = read_reference_index(index_path) if index_path.exists() else ReferenceIndex()
    state = (
        Checkpoint.model_validate_json(checkpoint_path.read_bytes())
        if checkpoint_path.exists()
        else Checkpoint()
    )
    cache_path = output / "collection-cache.json"
    cache = CollectionCache()
    if cache_path.exists() and not full_rescan and not urls:
        raw_cache = cache_path.read_bytes()
        if len(raw_cache) > 16_000_000:
            raise ValueError("The metadata collection cache exceeds 16 MB")
        saved_cache = CollectionCache.model_validate_json(raw_cache)
        if saved_cache.filter_version == FILTER_VERSION:
            cache = saved_cache
    if state.last_successful_scan and not index_path.exists():
        raise ValueError(
            "Checkpoint exists but its reference index is missing; restore the snapshot"
        )
    last_scan = _timestamp(state.last_successful_scan) if state.last_successful_scan else None
    if last_scan is not None and last_scan > now:
        raise ValueError("The saved checkpoint is in the future")
    replay = full_rescan or state.filter_version != FILTER_VERSION
    scan_from = (
        PROTEST_START
        if last_scan is None or replay
        else max(PROTEST_START, last_scan - timedelta(hours=overlap_hours))
    )
    policy = RobotsPolicy(fetch(ROBOTS_URL))
    if isinstance(fetch, PublicReader):
        fetch.policy = policy
    requests = 1

    def checked_fetch(url: str) -> str:
        nonlocal requests
        policy.check(url)
        requests += 1
        return fetch(url)

    candidates: dict[str, datetime | None] = {}
    sitemap_count = 0
    if urls:
        candidates = dict.fromkeys(urls)
    else:
        months = []
        first_month = (PROTEST_START.year, PROTEST_START.month)
        last_month = (
            now.astimezone(ZoneInfo("Europe/Tirane")).year,
            now.astimezone(ZoneInfo("Europe/Tirane")).month,
        )
        for url, _ in sitemap_entries(checked_fetch(SITEMAP_URL), index=True):
            match = _MONTH_PATH.fullmatch(urlparse(url).path)
            if match:
                month = (int(match.group(1)), int(match.group(2)))
                if not 1 <= month[1] <= 12:
                    raise ValueError("Sitemap contains an invalid month")
                if first_month <= month <= last_month:
                    months.append((month, url))
        expected = (last_month[0] - first_month[0]) * 12 + last_month[1] - first_month[1] + 1
        if len({month for month, _ in months}) != expected:
            raise ValueError("The sitemap index does not cover every month since 31 May 2026")
        if len(months) > max_sitemaps:
            raise ValueError("Sitemap limit would leave an incomplete scan; raise --max-sitemaps")
        for _, sitemap_url in sorted(set(months)):
            for url, modified in sitemap_entries(checked_fetch(sitemap_url), index=False):
                if url not in candidates or (
                    modified and (candidates[url] or PROTEST_START) < modified
                ):
                    candidates[url] = modified
            sitemap_count += 1
            if progress:
                progress({"stage": "sitemaps", "checked": sitemap_count, "total": len(months)})

    records = {
        current.key: current
        for article in previous.articles
        if (current := _current_reference(article)) is not None
    }
    observations = dict(state.observations)
    counts = dict(
        fetched=0,
        selected=0,
        new=0,
        updated=0,
        unchanged=0,
        cached=0,
        unrelated=0,
        before_start=0,
        candidates=0,
        resumed_from_cache=0,
        removed_unrelated=len(previous.articles) - len(records),
    )
    preview: list[dict[str, Any]] = []
    ordered = sorted(candidates.items(), key=lambda item: (item[1] or now, item[0]), reverse=True)
    for url, lastmod in ordered:
        path = urlparse(url).path
        path_date = re.match(r"/(\d{4}/\d{2}/\d{2})/", path)
        if (
            path_date
            and date.fromisoformat(path_date.group(1).replace("/", "-")) < PROTEST_START.date()
        ):
            counts["before_start"] += 1
            continue
        if not urls and not _CANDIDATE.search(unquote(path)):
            continue
        counts["candidates"] += 1
        observed = observations.get(url)
        if (
            not urls
            and not replay
            and observed is not None
            and lastmod is not None
            and observed.lastmod == _iso(lastmod)
            and lastmod < scan_from
        ):
            counts["cached"] += 1
            continue
        cached = cache.entries.get(url)
        reusable = (
            cached is not None
            and lastmod is not None
            and cached.observation.lastmod == _iso(lastmod)
            and lastmod <= _timestamp(cached.observation.checked_at) <= now
            and (last_scan is None or _timestamp(cached.observation.checked_at) > last_scan)
            and now - _timestamp(cached.observation.checked_at) <= timedelta(hours=24)
        )
        if reusable and cached is not None:
            article = _current_reference(cached.reference) if cached.reference else None
            cache.entries[url] = cached.model_copy(update={"reference": article})
            observations[url] = cached.observation
            counts["resumed_from_cache"] += 1
        else:
            if counts["fetched"] >= max_articles:
                raise ValueError(
                    "Article limit would leave an incomplete scan; raise --max-articles"
                )
            counts["fetched"] += 1
            if progress and (counts["fetched"] == 1 or counts["fetched"] % 25 == 0):
                progress(
                    {
                        "stage": "articles",
                        "fetched": counts["fetched"],
                        "selected": counts["selected"],
                        "cached": counts["cached"],
                        "resumed": counts["resumed_from_cache"],
                    }
                )
            article = extract_reference(checked_fetch(url), url, captured_at=now)
            observation = Observation(
                lastmod=_iso(lastmod) if lastmod else None, checked_at=_iso(now)
            )
            observations[url] = observation
            cache.entries[url] = CachedMetadata(observation=observation, reference=article)
            if not dry_run and not urls and counts["fetched"] % 25 == 0:
                _atomic_json(cache_path, cache)
        if article is None:
            counts["unrelated"] += 1
            continue
        counts["selected"] += 1
        existing = records.get(article.key)
        if existing is None:
            counts["new"] += 1
            records[article.key] = article
        else:
            article = article.model_copy(
                update={"aliases": sorted(set(existing.aliases + article.aliases))}
            )
            if existing.revision == article.revision:
                counts["unchanged"] += 1
            else:
                counts["updated"] += 1
                records[article.key] = article
        preview.append(
            {
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at,
                "topics": article.topics,
                "relevance_score": article.relevance_score,
            }
        )

    result = ReferenceIndex(articles=sorted(records.values(), key=lambda article: article.key))
    advance = not urls
    next_state = Checkpoint(
        last_successful_scan=_iso(now) if advance else state.last_successful_scan,
        filter_version=FILTER_VERSION if advance else state.filter_version,
        observations=observations,
    )
    if not dry_run:
        # Snapshot first, cursor last. A crash between them causes a safe replay.
        if not urls:
            _atomic_json(cache_path, cache)
        if result != previous or not index_path.exists():
            _atomic_json(index_path, result)
        _atomic_json(checkpoint_path, next_state)
    return {
        "mode": "dry-run" if dry_run else "write",
        "content_use": "reference",
        "scan_from": _iso(scan_from),
        "scan_until": _iso(now),
        "manual_urls": bool(urls),
        "sitemaps_checked": sitemap_count,
        "discovered_urls": len(candidates),
        "requests": requests,
        **counts,
        "total_saved_articles": len(result.articles),
        "checkpoint_advanced": advance and not dry_run,
        "last_successful_scan": next_state.last_successful_scan
        if not dry_run
        else state.last_successful_scan,
        "preview": sorted(preview, key=lambda item: -int(item["relevance_score"]))[:10],
        "cloud_writes": False,
        "model_calls": 0,
    }
