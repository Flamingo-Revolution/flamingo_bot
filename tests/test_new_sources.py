import json
from pathlib import Path

from flamingo_bot.parsers import parse_sources
from flamingo_bot.sources import ResolvedSource, SourceDefinition, SourceRule
from flamingo_bot.web_content import discover_news_urls, extract_main_text


def _source(repository: Path, parser: str, pattern: str) -> ResolvedSource:
    return ResolvedSource(
        definition=SourceDefinition(
            id="flamingo-revolution",
            label="Harta e Protestave",
            repository_setting="unused",
            language="sq",
            base_url="https://www.flamingorevolution.eu/harta/",
            rules=[SourceRule(parser=parser, include=[pattern])],
        ),
        repository_path=repository.resolve(),
        revision="a" * 40,
    )


def test_map_ingests_each_city_and_protest_with_day_counts(tmp_path: Path) -> None:
    data = tmp_path / "data" / "locations.json"
    data.parent.mkdir()
    data.write_text(
        json.dumps(
            [
                {
                    "id": "berlin-de",
                    "title": "Berlin",
                    "city": "Berlin",
                    "country": "Germany",
                    "latitude": 52.5,
                    "longitude": 13.4,
                    "type": "protest-city",
                    "chapterActive": True,
                    "protestCount": 3,
                    "protestRecordCount": 1,
                    "protests": [
                        {
                            "id": "berlin-1",
                            "title": "Marshoj për Shqipërinë",
                            "startDate": "2026-09-01",
                            "endDate": "2026-09-03",
                            "dayCount": 3,
                            "description": "Takim qytetar paqësor në qendër të qytetit.",
                            "sourceUrl": "https://example.org/event",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    documents, issues, discovered = parse_sources([_source(tmp_path, "map_json", "data/*.json")])

    assert issues == []
    assert discovered == 1
    assert {document.content_type for document in documents} == {
        "map-city", "map-protest", "map-overview"
    }
    city = next(document for document in documents if document.content_type == "map-city")
    protest = next(document for document in documents if document.content_type == "map-protest")
    overview = next(document for document in documents if document.content_type == "map-overview")
    assert "Protest days for this city: 3" in city.text
    assert "Protest days in this record: 3" in protest.text
    assert "https://example.org/event" in protest.text
    assert overview.metadata["protest_record_count"] == 1
    assert overview.metadata["protest_day_count"] == 3
    assert "1 qytete ose lokacione" in overview.text
    assert "3 ditë proteste" in overview.text
    assert all(document.canonical_url.endswith("/harta/") for document in documents)


def test_news_discovery_and_article_extraction_ignore_navigation() -> None:
    index = (
        '<a href="/news/first/">First</a>'
        '<a href="https://other.example/news/foreign/">Foreign</a>'
        '<a href="/news/first/?utm=x">Duplicate</a>'
        '<a href="/news/">Index</a>'
    )
    assert discover_news_urls(index, "https://www.flamingorevolution.eu/news/") == [
        "https://www.flamingorevolution.eu/news/first/"
    ]

    page = (
        '<nav>Ignore this navigation</nav>'
        '<main class="news-article"><h1>Public report</h1>'
        '<p>A sufficiently detailed public article with useful factual context and more than '
        'eighty characters to support meaningful retrieval.</p>'
        '<script>Ignore this script</script></main>'
    )
    title, body = extract_main_text(page, required_class="news-article")
    assert title == "Public report"
    assert "useful factual context" in body
    assert "Ignore this navigation" not in body
    assert "Ignore this script" not in body
