import asyncio
import html
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.request import Request

import pytest

from flamingo_bot.chunking import SemanticChunker
from flamingo_bot.config import Settings
from flamingo_bot.lapsi import (
    BASE_URL,
    MAX_EXCERPT_CHARS,
    PROTEST_START,
    ROBOTS_URL,
    SITEMAP_URL,
    ArticleReference,
    Checkpoint,
    PublicReader,
    ReferenceIndex,
    RobotsPolicy,
    _SafeRedirect,
    _writer_lock,
    extract_reference,
    normalize_url,
    protest_topics,
    read_reference_index,
    run_snapshot,
    sitemap_entries,
)
from flamingo_bot.models import RetrievedChunk
from flamingo_bot.parsers import parse_sources
from flamingo_bot.prompting import SYSTEM_INSTRUCTIONS, build_grounded_prompt
from flamingo_bot.providers.memory import HashEmbedder, InMemoryVectorStore, ScriptedAnswerGenerator
from flamingo_bot.retrieval import ChatService, diversify
from flamingo_bot.sources import (
    SourceCatalog,
    content_revision,
    load_source_catalog,
    resolve_sources,
)
from scripts.snapshot_lapsi import main

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
FIRST = BASE_URL + "/2026/05/31/protesta-flamingo-ne-tirane"
RECENT = BASE_URL + "/studentet-ne-proteste-ne-tirane-i2002"
POLICY = "User-agent: *\nContent-Signal: search=yes,ai-train=no,use=reference\nAllow: /\n"


def page(
    url: str,
    *,
    title: str = "Protesta Flamingo në Tiranë",
    excerpt: str = "Protestuesit në Tiranë marshuan për mbrojtjen e zonave të mbrojtura.",
    published: str = "2026-05-31T20:00:00+02:00",
    section: str = "Lajme",
    body: str = "FULL ARTICLE BODY MUST NOT BE STORED",
) -> str:
    values = {
        "og:title": title,
        "og:description": excerpt,
        "article:published_time": published,
        "article:modified_time": published,
        "article:section": section,
    }
    tags = "".join(
        f'<meta property="{key}" content="{html.escape(value, quote=True)}">'
        for key, value in values.items()
    )
    return (
        f'<html><head>{tags}<link rel="canonical" href="{url}"></head>'
        f"<body><nav>Unrelated sports recommendations</nav><article>{body}</article></body></html>"
    )


def sitemap(entries: list[tuple[str, str]], *, index: bool = False) -> str:
    root, item = ("sitemapindex", "sitemap") if index else ("urlset", "url")
    body = "".join(
        f"<{item}><loc>{html.escape(url)}</loc><lastmod>{modified}</lastmod></{item}>"
        for url, modified in entries
    )
    return f'<{root} xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</{root}>'


class Site:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.pages = {ROBOTS_URL: POLICY}
        self.entries = {
            FIRST: "2026-05-31T18:00:00Z",
            RECENT: "2026-10-17T18:00:00Z",
        }
        self.pages[FIRST] = page(FIRST)
        self.pages[RECENT] = page(
            RECENT,
            title="Studentët në protestë në Tiranë",
            excerpt="Studentët protestuan në Tiranë dhe kërkuan hetimin e veprimeve të policisë.",
            published="2026-10-17T20:00:00+02:00",
        )
        self.failed_url: str | None = None

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url == self.failed_url:
            raise OSError("Synthetic network failure")
        if url in self.pages:
            return self.pages[url]
        if url == SITEMAP_URL:
            return sitemap(
                [
                    (BASE_URL + f"/sitemaps/sitemap-2026-{month:02d}.xml", "2026-10-18T10:00:00Z")
                    for month in range(5, 11)
                ],
                index=True,
            )
        month = url.rsplit("-", 1)[-1][:2]
        return sitemap(
            [
                (article, modified)
                for article, modified in self.entries.items()
                if ("05" if "/2026/05/" in article else "10") == month
            ]
        )


def read_state(root: Path) -> Checkpoint:
    return Checkpoint.model_validate_json((root / "checkpoint.json").read_bytes())


def resolved_lapsi(root: Path):
    settings = Settings(_env_file=None, flamingo_lapsi_dir=root)
    definition = next(
        source
        for source in load_source_catalog(settings.source_catalog_path).sources
        if source.id == "lapsi"
    )
    return resolve_sources(settings, SourceCatalog(version=1, sources=[definition]))


def test_backfill_includes_31_may_and_preserves_all_selected_articles(tmp_path: Path) -> None:
    site = Site()
    earlier = BASE_URL + "/2026/05/30/protesta-flamingo-ne-tirane"
    site.entries[earlier] = "2026-10-17T19:00:00Z"
    site.pages[earlier] = page(earlier, published="2026-05-30T18:00:00+02:00")

    report = run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)

    assert report["scan_from"] == PROTEST_START.astimezone(UTC).isoformat()
    assert report["sitemaps_checked"] == 6
    assert report["new"] == 2
    assert report["before_start"] == 1
    assert earlier not in site.calls
    assert read_state(tmp_path).last_successful_scan == NOW.isoformat()
    assert len(read_reference_index(tmp_path / "index.json").articles) == 2
    assert report["checkpoint_advanced"] is True
    assert report["model_calls"] == 0
    assert report["cloud_writes"] is False


def test_dry_run_creates_no_files_and_no_checkpoint(tmp_path: Path) -> None:
    output = tmp_path / "does-not-exist"
    report = run_snapshot(output, dry_run=True, fetch=Site(), now=NOW)
    assert report["new"] == 2
    assert report["checkpoint_advanced"] is False
    assert report["last_successful_scan"] is None
    assert not output.exists()


def test_repeat_run_rechecks_overlap_without_rewriting_unchanged_snapshots(tmp_path: Path) -> None:
    site = Site()
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    before = (tmp_path / "index.json").read_bytes()
    site.calls.clear()

    later = NOW + timedelta(hours=1)
    report = run_snapshot(tmp_path, dry_run=False, fetch=site, now=later)

    assert FIRST not in site.calls
    assert RECENT in site.calls
    assert report["cached"] == 1
    assert report["unchanged"] == 1
    assert report["new"] == 0
    assert report["scan_from"] == (NOW - timedelta(hours=48)).isoformat()
    assert (tmp_path / "index.json").read_bytes() == before
    assert read_state(tmp_path).last_successful_scan == later.isoformat()


def test_incremental_run_finds_new_late_indexed_and_edited_old_articles(tmp_path: Path) -> None:
    site = Site()
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    site.calls.clear()
    site.entries[FIRST] = "2026-10-20T18:00:00Z"
    site.pages[FIRST] = page(
        FIRST, excerpt="Protesta Flamingo në Tiranë ka një njoftim të korrigjuar."
    )
    late = BASE_URL + "/protesta-flamingo-raport-historik-i2003"
    new = BASE_URL + "/studentet-protestojne-ne-tirane-i2004"
    site.entries[late] = "2026-07-01T18:00:00Z"
    site.entries[new] = "2026-10-20T18:00:00Z"
    site.pages[late] = page(late, published="2026-07-01T20:00:00+02:00")
    site.pages[new] = page(new, published="2026-10-20T20:00:00+02:00")

    report = run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW + timedelta(days=3))

    assert {FIRST, late, new} <= set(site.calls)
    assert RECENT in site.calls  # Rechecked inside the previous checkpoint's overlap.
    assert report["new"] == 2
    assert report["updated"] == 1
    assert report["total_saved_articles"] == 4


def test_failure_leaves_previous_snapshot_and_checkpoint_unchanged(tmp_path: Path) -> None:
    site = Site()
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    before = {name: (tmp_path / name).read_bytes() for name in ("index.json", "checkpoint.json")}
    site.failed_url = RECENT

    with pytest.raises(OSError, match="Synthetic network failure"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW + timedelta(hours=1))

    assert before == {name: (tmp_path / name).read_bytes() for name in before}


@pytest.mark.parametrize("limit", ["articles", "sitemaps"])
def test_resource_limits_never_advance_an_incomplete_scan(tmp_path: Path, limit: str) -> None:
    options = {f"max_{limit}": 1}
    with pytest.raises(ValueError, match="incomplete scan"):
        run_snapshot(tmp_path, dry_run=False, fetch=Site(), now=NOW, **options)
    assert not (tmp_path / "index.json").exists()
    assert not (tmp_path / "checkpoint.json").exists()


def test_missing_month_is_an_incomplete_backfill(tmp_path: Path) -> None:
    site = Site()
    site.pages[SITEMAP_URL] = sitemap(
        [(BASE_URL + "/sitemaps/sitemap-2026-10.xml", "2026-10-18T10:00:00Z")], index=True
    )
    with pytest.raises(ValueError, match="every month"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    assert not (tmp_path / "checkpoint.json").exists()


def test_hand_picked_urls_do_not_skip_the_initial_global_backfill(tmp_path: Path) -> None:
    site = Site()
    report = run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW, urls=[RECENT])
    assert report["checkpoint_advanced"] is False
    assert read_state(tmp_path).last_successful_scan is None
    assert SITEMAP_URL not in site.calls

    report = run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    assert report["scan_from"] == PROTEST_START.astimezone(UTC).isoformat()
    assert report["total_saved_articles"] == 2


def test_dry_run_does_not_change_an_existing_index(tmp_path: Path) -> None:
    site = Site()
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    before = {name: (tmp_path / name).read_bytes() for name in ("index.json", "checkpoint.json")}
    run_snapshot(tmp_path, dry_run=True, fetch=site, now=NOW + timedelta(hours=1))
    assert before == {name: (tmp_path / name).read_bytes() for name in before}


def test_full_rescan_revisits_old_articles(tmp_path: Path) -> None:
    site = Site()
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    site.calls.clear()
    report = run_snapshot(
        tmp_path,
        dry_run=True,
        fetch=site,
        now=NOW + timedelta(days=3),
        full_rescan=True,
    )
    assert FIRST in site.calls
    assert RECENT in site.calls
    assert report["scan_from"] == PROTEST_START.astimezone(UTC).isoformat()


def test_metadata_extraction_never_retains_body_navigation_or_operator_data() -> None:
    body = 'PRIVATE DATA <head><meta name="description" content="UNRELATED BODY"></head>'
    article = extract_reference(page(FIRST, body=body), FIRST, captured_at=NOW)
    assert article is not None
    data = article.model_dump_json()
    assert "PRIVATE DATA" not in data
    assert "UNRELATED BODY" not in data
    assert "sports recommendations" not in data
    assert article.content_use == "reference"
    assert article.excerpt_kind == "publisher-description"
    assert article.published_at == "2026-05-31T18:00:00+00:00"


def test_excerpt_is_bounded_without_using_article_body() -> None:
    article = extract_reference(
        page(FIRST, excerpt="Protesta Flamingo. " * 50), FIRST, captured_at=NOW
    )
    assert article is not None
    assert len(article.publisher_excerpt) <= MAX_EXCERPT_CHARS
    assert article.publisher_excerpt.endswith("…")


@pytest.mark.parametrize("published", ["2026-05-30T20:00:00+02:00", "2026-10-19T20:00:00+02:00"])
def test_publication_date_bounds_are_checked_from_metadata(published: str) -> None:
    assert extract_reference(page(FIRST, published=published), FIRST, captured_at=NOW) is None


@pytest.mark.parametrize(
    "title,excerpt,section",
    [
        (
            "Protesta e futbollistëve në Tiranë",
            "Protestuesit kundërshtojnë vendimin e arbitrit.",
            "Sport",
        ),
        ("Protesta në Paris", "Studentët francezë marshuan kundër një ligji në Francë.", "Lajme"),
        (
            "Studentët në Tiranë",
            "Hapet programi i bursave të universitetit për vitin e ri.",
            "Lajme",
        ),
        ("Berisha thërret protestë në Tiranë", "Partia Demokratike njofton tubimin e PD.", "Lajme"),
        (
            "Afera e re e korrupsionit",
            "Një çështje e palidhur me protestën në një qytet tjetër.",
            "Lajme",
        ),
    ],
)
def test_unrelated_news_is_not_selected(title: str, excerpt: str, section: str) -> None:
    assert protest_topics(title, excerpt, section) == (0, [])


def test_diaspora_flamingo_reporting_remains_in_scope() -> None:
    score, topics = protest_topics(
        "Revolucioni Flamingo në Paris",
        "Diaspora marshon në mbështetje të protestës.",
    )
    assert score == 100
    assert topics == ["flamingo-movement"]


@pytest.mark.parametrize("signal", ["search=no", "ai-input=no", "use=immediate"])
def test_content_signals_are_checked_before_collecting_articles(signal: str) -> None:
    with pytest.raises(ValueError, match="disallow"):
        RobotsPolicy("User-agent: *\nContent-Signal: " + signal + "\nAllow: /\n")


def test_ai_training_opt_out_does_not_mistake_reference_indexing_for_training() -> None:
    policy = RobotsPolicy(POLICY)
    policy.check(FIRST)
    assert policy.signals["ai-train"] == "no"
    assert policy.signals["use"] == "reference"


def test_robot_disallow_stops_fetching_without_a_checkpoint(tmp_path: Path) -> None:
    site = Site()
    site.pages[ROBOTS_URL] = POLICY + "Disallow: /sitemap.xml\n"
    with pytest.raises(ValueError, match=r"robots\.txt disallows"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    assert site.calls == [ROBOTS_URL]
    assert not (tmp_path / "checkpoint.json").exists()


def test_redirects_cannot_leave_the_approved_domain() -> None:
    handler = _SafeRedirect(lambda url: normalize_url(url))
    with pytest.raises(ValueError, match="Only public HTTPS"):
        handler.redirect_request(Request(FIRST), None, 302, "Redirect", {}, "https://example.org/")


@pytest.mark.parametrize(
    "url",
    [
        "http://lapsi.al/story-i2001",
        "https://lapsi.al.evil.test/story-i2001",
        "https://user@lapsi.al/story-i2001",
        "https://lapsi.al:443/story-i2001",
        "https://127.0.0.1/story-i2001",
        "file:///etc/passwd",
        "https://lapsi.al/kategoria/lajme/",
        "https://lapsi.al/%2e%2e/story-i2001",
    ],
)
def test_only_lapsi_article_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(url, article=True)


def test_tracking_parameters_and_duplicate_aliases_are_removed() -> None:
    assert normalize_url(RECENT + "?utm_source=example#fragment", article=True) == RECENT


def test_sitemap_optional_pagemap_does_not_hide_core_urls() -> None:
    xml = sitemap([(RECENT, "2026-10-17T18:00:00Z")]).replace(
        "</url>",
        '<PageMap><DataObject value="image?a=1&b=2"/></PageMap></url>',
    )
    assert sitemap_entries(xml, index=False) == [(RECENT, datetime(2026, 10, 17, 18, tzinfo=UTC))]


def test_sitemap_namespaced_headline_extension_can_be_malformed() -> None:
    xml = sitemap([(RECENT, "2026-10-17T18:00:00Z")]).replace(
        "</url>",
        '<pagemap:PageMap xmlns:pagemap="https://example.org/pagemap">'
        '<pagemap:Attribute name="headline">VIDEO&FOTO</pagemap:Attribute>'
        "</pagemap:PageMap></url>",
    )
    assert sitemap_entries(xml, index=False)[0][0] == RECENT


def test_unrelated_malformed_legacy_resource_is_not_fetched() -> None:
    xml = sitemap([(BASE_URL + "/Del VIDEO/ An unrelated story", "2026-10-17T18:00:00Z")])
    assert sitemap_entries(xml, index=False) == []


def test_unsupported_protest_url_blocks_incomplete_coverage() -> None:
    xml = sitemap([(BASE_URL + "/Del VIDEO/ Protesta studentore", "2026-10-17T18:00:00Z")])
    with pytest.raises(ValueError, match="Unsupported protest article"):
        sitemap_entries(xml, index=False)


@pytest.mark.parametrize("xml", ["<urlset><url>", "<!DOCTYPE urlset><urlset/>", "<sitemapindex/>"])
def test_malformed_or_unsupported_sitemaps_fail_closed(xml: str) -> None:
    with pytest.raises(ValueError):
        sitemap_entries(xml, index=False)


def test_duplicate_canonical_articles_are_stored_once(tmp_path: Path) -> None:
    site = Site()
    alias = BASE_URL + "/2026/10/17/studentet-ne-proteste-ne-tirane"
    site.pages[alias] = site.pages[RECENT]
    report = run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW, urls=[alias, RECENT])
    assert report["total_saved_articles"] == 1
    assert set(read_reference_index(tmp_path / "index.json").articles[0].aliases) == {alias, RECENT}


def test_local_schema_rejects_raw_html_and_unknown_fields() -> None:
    article = extract_reference(page(FIRST), FIRST, captured_at=NOW)
    assert article is not None
    with pytest.raises(ValueError, match="Extra inputs"):
        ArticleReference.model_validate({**article.model_dump(), "raw_html": "DO NOT INGEST"})


def test_parser_cites_lapsi_and_distinguishes_publication_from_incident_date(
    tmp_path: Path,
) -> None:
    run_snapshot(tmp_path, dry_run=False, fetch=Site(), now=NOW)
    documents, issues, discovered = parse_sources(resolved_lapsi(tmp_path))
    assert issues == []
    assert discovered == 1
    assert len(documents) == 2
    assert {document.source_id for document in documents} == {"lapsi"}
    assert {document.source_label for document in documents} == {"Lapsi.al"}
    assert {document.canonical_url for document in documents} == {FIRST, RECENT}
    assert all(document.metadata["content_use"] == "reference" for document in documents)
    assert all("event_date" not in document.metadata for document in documents)
    assert all(
        "not an established incident date" in document.sticky_context for document in documents
    )
    assert all("FULL ARTICLE BODY" not in document.text for document in documents)
    assert all(len(document.revision) == 64 for document in documents)
    chunks = SemanticChunker().chunk_documents(documents)
    assert all(chunk.metadata["evidence_access"] == "publisher_metadata" for chunk in chunks)


def test_checkpoint_only_changes_do_not_change_ingestion_fingerprint(tmp_path: Path) -> None:
    site = Site()
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    source = resolved_lapsi(tmp_path)[0]
    before = content_revision(tmp_path, source.definition)
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW + timedelta(hours=1))
    assert content_revision(tmp_path, source.definition) == before


def test_unconfigured_lapsi_is_optional() -> None:
    settings = Settings(_env_file=None)
    definition = next(
        source
        for source in load_source_catalog(settings.source_catalog_path).sources
        if source.id == "lapsi"
    )
    assert resolve_sources(settings, SourceCatalog(version=1, sources=[definition])) == []


def test_lapsi_references_are_reachable_by_retrieval() -> None:
    async def scenario() -> None:
        embedder = HashEmbedder()
        question = "Find Lapsi reporting about student protests in Tirana"
        vector = (await embedder.embed_texts([question]))[0]
        chunk = RetrievedChunk(
            chunk_id="lapsi-example",
            source_id="lapsi",
            source_label="Lapsi.al",
            title="Student protest report",
            text="Protest reference only",
            canonical_url=RECENT,
            distance=0,
        )
        service = ChatService(
            settings=Settings(_env_file=None),
            embedder=embedder,
            vector_store=InMemoryVectorStore([(chunk, vector)]),
            answer_generator=ScriptedAnswerGenerator("Unused"),
        )
        hits = await service.retrieve(question)
        assert [hit.source_id for hit in hits] == ["lapsi"]

    asyncio.run(scenario())


def test_references_do_not_duplicate_detailed_research_of_the_same_article() -> None:
    detailed = RetrievedChunk(
        chunk_id="research",
        source_id="diaspora-zbarkon",
        source_label="Protest research",
        title="Research notes",
        text="Detailed attributed reporting summary",
        canonical_url=RECENT,
        distance=0.2,
    )
    reference = detailed.model_copy(
        update={
            "chunk_id": "reference",
            "source_id": "lapsi",
            "distance": 0.1,
            "metadata": {"content_use": "reference"},
        }
    )
    assert [hit.chunk_id for hit in diversify([reference, detailed], 7)] == ["research"]


def test_reference_only_limitation_is_visible_to_answer_generation() -> None:
    reference = RetrievedChunk(
        chunk_id="reference",
        source_id="lapsi",
        source_label="Lapsi.al",
        title="Protest report",
        text="Publisher-provided introduction",
        canonical_url=RECENT,
        distance=0.1,
        metadata={"content_use": "reference", "evidence_access": "publisher_metadata"},
    )
    prompt = build_grounded_prompt("What happened?", [reference])
    assert "Reference-only use" in prompt
    assert "do not summarize its full text" in prompt
    assert "original page was not re-fetched during ingestion" not in prompt
    assert "do not generate an article summary" in SYSTEM_INSTRUCTIONS


def test_cli_reports_failures_without_claiming_checkpoint_success(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def fail(*args, **kwargs):
        raise ValueError("Synthetic incomplete scan")

    monkeypatch.setattr("scripts.snapshot_lapsi.run_snapshot", fail)
    assert main(["--dry-run", "--output", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().err)["checkpoint_advanced"] is False


def test_reader_rejects_an_unbounded_or_too_fast_delay() -> None:
    with pytest.raises(ValueError, match="delay"):
        PublicReader(delay=0)


def test_reader_stops_after_head_instead_of_downloading_full_article(monkeypatch) -> None:
    head = b'<html><head><meta property="og:title" content="Protest report"></head>'
    response = BytesIO(head + b"<body>" + b"unneeded article body " * 100_000)
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"

    class Opener:
        def open(self, request, timeout):
            response.headers = headers
            response.geturl = lambda: RECENT
            return nullcontext(response)

    reader = PublicReader()
    reader.opener = Opener()
    result = reader(RECENT)
    assert result.encode() == head
    assert response.tell() <= 8192


def test_robot_rules_use_longest_path_and_allow_wins_ties() -> None:
    policy = RobotsPolicy("User-agent: *\nDisallow: /\nAllow: /studentet\nDisallow: /studentet\n")
    policy.check(RECENT)
    with pytest.raises(ValueError, match="disallows"):
        policy.check(FIRST)


def test_specific_robot_group_overrides_wildcard_and_other_bot_rules() -> None:
    policy = RobotsPolicy(
        "User-agent: *\nDisallow: /\n"
        "User-agent: FlamingoBotProtestReferences\nDisallow:\n"
        "User-agent: GPTBot\nDisallow: /\n"
    )
    policy.check(RECENT)


def test_writer_lock_prevents_competing_collectors(tmp_path: Path) -> None:
    with _writer_lock(tmp_path, dry_run=False):
        with pytest.raises(ValueError, match="already running"):
            run_snapshot(tmp_path, dry_run=False, fetch=Site(), now=NOW)


def test_missing_index_cannot_silently_resume_after_a_checkpoint(tmp_path: Path) -> None:
    (tmp_path / "checkpoint.json").write_text(
        Checkpoint(last_successful_scan=NOW.isoformat()).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reference index is missing"):
        run_snapshot(tmp_path, dry_run=False, fetch=Site(), now=NOW + timedelta(hours=1))


def test_filter_version_changes_require_a_full_historical_recheck(tmp_path: Path) -> None:
    site = Site()
    run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW)
    old = read_state(tmp_path).model_copy(update={"filter_version": "protest-topics/0"})
    (tmp_path / "checkpoint.json").write_text(old.model_dump_json(), encoding="utf-8")
    site.calls.clear()
    report = run_snapshot(tmp_path, dry_run=True, fetch=site, now=NOW + timedelta(days=3))
    assert report["scan_from"] == PROTEST_START.astimezone(UTC).isoformat()
    assert FIRST in site.calls


def many_articles() -> Site:
    site = Site()
    site.entries = {}
    for number in range(30):
        url = BASE_URL + f"/protesta-flamingo-ne-tirane-i{3000 + number}"
        site.entries[url] = "2026-10-17T18:00:00Z"
        site.pages[url] = page(url, published="2026-10-17T20:00:00+02:00")
    return site


def test_capped_backfill_resumes_cached_metadata_without_a_success_checkpoint(
    tmp_path: Path,
) -> None:
    site = many_articles()
    with pytest.raises(ValueError, match="incomplete scan"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW, max_articles=25)
    assert not (tmp_path / "index.json").exists()
    assert not (tmp_path / "checkpoint.json").exists()
    before = (tmp_path / "collection-cache.json").read_bytes()
    site.calls.clear()

    preview = run_snapshot(tmp_path, dry_run=True, fetch=site, now=NOW + timedelta(minutes=1))

    assert preview["resumed_from_cache"] == 25
    assert preview["fetched"] == 5
    assert preview["new"] == 30
    assert preview["checkpoint_advanced"] is False
    assert (tmp_path / "collection-cache.json").read_bytes() == before

    saved = run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW + timedelta(minutes=2))
    assert saved["resumed_from_cache"] == 25
    assert saved["fetched"] == 5
    assert saved["checkpoint_advanced"] is True
    assert len(read_reference_index(tmp_path / "index.json").articles) == 30


def test_changed_sitemap_entry_is_not_reused_from_an_interrupted_run(tmp_path: Path) -> None:
    site = many_articles()
    with pytest.raises(ValueError, match="incomplete scan"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW, max_articles=25)
    cached = json.loads((tmp_path / "collection-cache.json").read_text())["entries"]
    changed = next(iter(cached))
    site.entries[changed] = "2026-10-18T12:00:00Z"
    report = run_snapshot(tmp_path, dry_run=True, fetch=site, now=NOW + timedelta(minutes=1))
    assert report["resumed_from_cache"] == 24
    assert report["fetched"] == 6


def test_interrupted_run_cache_expires_after_24_hours(tmp_path: Path) -> None:
    site = many_articles()
    with pytest.raises(ValueError, match="incomplete scan"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW, max_articles=25)
    report = run_snapshot(tmp_path, dry_run=True, fetch=site, now=NOW + timedelta(days=2))
    assert report["resumed_from_cache"] == 0
    assert report["fetched"] == 30


def test_full_rescan_does_not_use_an_interrupted_run_cache(tmp_path: Path) -> None:
    site = many_articles()
    with pytest.raises(ValueError, match="incomplete scan"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW, max_articles=25)
    report = run_snapshot(tmp_path, dry_run=True, fetch=site, now=NOW, full_rescan=True)
    assert report["resumed_from_cache"] == 0
    assert report["fetched"] == 30


def test_resume_still_checks_current_crawl_policy(tmp_path: Path) -> None:
    site = many_articles()
    with pytest.raises(ValueError, match="incomplete scan"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW, max_articles=25)
    site.pages[ROBOTS_URL] = "User-agent: *\nContent-Signal: search=no\n"
    with pytest.raises(ValueError, match="content signals disallow"):
        run_snapshot(tmp_path, dry_run=False, fetch=site, now=NOW + timedelta(minutes=1))
    assert not (tmp_path / "checkpoint.json").exists()


def test_unrelated_diaspora_protests_are_not_mistaken_for_flamingo_coverage() -> None:
    assert protest_topics(
        "Komuniteti shqiptaro-amerikan paralajmëron protestë në New York",
        "Shqiptarët në SHBA organizuan një protestë për procesin në Hagë dhe Kosovën.",
    ) == (0, [])
    score, _ = protest_topics(
        "Diaspora shqiptare në SHBA proteston kundër Ramës",
        "Protestuesit kërkojnë dorëheqjen e Ramës dhe mbështesin marshimet në Tiranë.",
    )
    assert score > 0


def test_saved_references_are_rechecked_against_current_topic_rules(tmp_path: Path) -> None:
    unrelated = ArticleReference(
        url=BASE_URL + "/protesta-per-hagen-i9999",
        title="Komuniteti shqiptaro-amerikan proteston për Kosovën",
        publisher_excerpt="Shqiptarët në SHBA organizuan një protestë për procesin në Hagë.",
        published_at="2026-10-17T18:00:00+00:00",
        captured_at=NOW.isoformat(),
        topics=["protest-events"],
        relevance_score=40,
    )
    (tmp_path / "index.json").write_text(
        ReferenceIndex(articles=[unrelated]).model_dump_json(),
        encoding="utf-8",
    )
    report = run_snapshot(tmp_path, dry_run=False, fetch=Site(), now=NOW)
    assert report["removed_unrelated"] == 1
    assert report["total_saved_articles"] == 2
    assert "9999" not in {
        article.key for article in read_reference_index(tmp_path / "index.json").articles
    }


@pytest.mark.parametrize("url", [BASE_URL + "/.env", BASE_URL + "/%2eenv"])
def test_hidden_files_are_not_article_urls(url: str) -> None:
    with pytest.raises(ValueError, match="article URL"):
        normalize_url(url, article=True)


def test_cli_interrupt_is_reported_without_a_local_path_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("scripts.snapshot_lapsi.run_snapshot", interrupt)
    assert main(["--write", "--output", str(tmp_path)]) == 130
    assert json.loads(capsys.readouterr().err)["interrupted"] is True
