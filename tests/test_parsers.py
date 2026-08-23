from pathlib import Path

from flamingo_bot.parsers import parse_sources
from flamingo_bot.sources import ResolvedSource, SourceDefinition, SourceRule

FIXTURES = Path(__file__).parent / "fixtures"


def resolved_source(
    source_id: str,
    repository: Path,
    rules: list[SourceRule],
    base_url: str,
) -> ResolvedSource:
    return ResolvedSource(
        definition=SourceDefinition(
            id=source_id,
            label=source_id.replace("-", " ").title(),
            repository_setting="unused",
            language="sq",
            base_url=base_url,
            rules=rules,
        ),
        repository_path=repository.resolve(),
        revision="a" * 40,
    )


def test_dossier_parser_preserves_status_sources_and_relations() -> None:
    source = resolved_source(
        "flamingo-dossier",
        FIXTURES / "dossier",
        [
            SourceRule(
                parser="dossier_csv",
                include=["data/dosje.csv"],
                options={"relations_path": "data/lidhje.csv"},
            )
        ],
        "https://flamingo-revolution.github.io/flamingo-dossier/",
    )

    documents, issues, discovered = parse_sources([source])

    assert discovered == 1
    assert issues == []
    assert len(documents) == 1
    document = documents[0]
    assert document.legal_status == "active-investigation"
    assert "no final judgment" in document.text.lower()
    assert "Example Person" in document.text
    assert document.metadata["source_urls"] == [
        "https://example.org/source-one",
        "https://example.org/source-two",
    ]
    assert document.metadata["extracted_claim"] is True


def test_public_text_markdown_and_typescript_are_extracted() -> None:
    revolution = resolved_source(
        "flamingo-revolution",
        FIXTURES / "revolution-site",
        [
            SourceRule(parser="text", include=["public/llms.txt"]),
            SourceRule(parser="markdown", include=["src/content/**/*.md"]),
            SourceRule(parser="typescript_strings", include=["src/data/*.ts"]),
        ],
        "https://www.flamingorevolution.eu/",
    )
    diaspora = resolved_source(
        "diaspora-zbarkon",
        FIXTURES / "diaspora-zbarkon",
        [
            SourceRule(parser="markdown", include=["specs/Mission.md"]),
            SourceRule(parser="typescript_strings", include=["lib/content.ts"]),
        ],
        "https://www.diaspora-zbarkon.com/",
    )

    documents, issues, discovered = parse_sources([revolution, diaspora])

    assert discovered == 5
    assert issues == []
    assert len(documents) == 5
    by_path = {document.relative_path: document for document in documents}
    assert by_path["src/content/blog/test-post/index.md"].canonical_url.endswith("/blog/test-post/")
    assert "expose a secret" not in by_path["src/content/blog/test-post/index.md"].text
    assert "must not become evidence" not in by_path["src/content/blog/test-post/index.md"].text
    assert "hidden editorial note" not in by_path["src/content/blog/test-post/index.md"].text
    assert "volunteer-led public-interest mission" in by_path["src/data/about.ts"].text
    assert "private implementation comment" not in by_path["src/data/about.ts"].text
    assert "block comment with enough words" not in by_path["src/data/about.ts"].text
    assert "diaspora comes home" in by_path["lib/content.ts"].text


def participation_source() -> ResolvedSource:
    return resolved_source(
        "diaspora-zbarkon",
        FIXTURES / "diaspora-zbarkon",
        [SourceRule(parser="participation_ts", include=["data/participation.ts"])],
        "https://www.diaspora-zbarkon.com/",
    )


def test_participation_parser_keeps_the_figures_the_string_extractor_drops() -> None:
    documents, issues, discovered = parse_sources([participation_source()])

    assert discovered == 1
    assert issues == []
    by_section = {document.metadata["section"]: document for document in documents}
    assert set(by_section) == {"methodology", "index", "days", "events"}

    index = by_section["index"]
    assert "Day 2, 2026-06-06 (6 qershor 2026 / 6 June 2026), e shtunë / Saturday: peak 100" in (
        index.text
    )
    assert "Day 1, 2026-05-31 (31 maj 2026 / 31 May 2026): peak 4.78, mean 3.89, median 4.22." in (
        index.text
    )
    assert index.metadata["day_count"] == 3
    assert index.metadata["measured_day_count"] == 2
    assert index.metadata["index_100_day"] == 2


def test_participation_parser_labels_every_chunk_as_an_estimate() -> None:
    documents, _, _ = parse_sources([participation_source()])

    for document in documents:
        assert document.legal_status is not None
        assert "not an official count" in document.legal_status
        assert "not a number of people" in document.sticky_context
        assert "crowd-counting model" in document.sticky_context
        assert document.canonical_url == "https://www.diaspora-zbarkon.com/pulsi/"


def test_participation_parser_keeps_notes_with_their_day() -> None:
    documents, _, _ = parse_sources([participation_source()])
    days = next(document for document in documents if document.metadata["section"] == "days")

    paragraph = next(part for part in days.text.split("\n\n") if part.startswith("Day 2,"))
    assert "peak 100, mean 19.43, median 15.76" in paragraph
    assert "Dita më e madhe" in paragraph
    assert "The biggest day" in paragraph
    assert "https://example.org/day-two" in paragraph
    # The livestream URL is rebuilt from the file's own `yt` template.
    assert "https://www.youtube.com/watch?v=peakStream" in paragraph


def test_participation_parser_reports_an_unpublished_day_without_inventing_a_figure() -> None:
    documents, _, _ = parse_sources([participation_source()])
    days = next(document for document in documents if document.metadata["section"] == "days")

    paragraph = next(part for part in days.text.split("\n\n") if part.startswith("Day 3,"))
    assert "peak not published, mean not published, median not published" in paragraph


def test_participation_parser_carries_the_methodology_comment_and_events() -> None:
    documents, _, _ = parse_sources([participation_source()])
    by_section = {document.metadata["section"]: document for document in documents}

    methodology = by_section["methodology"].text
    assert "crowd-counting model" in methodology
    assert "Normalization reference: index 100 = day 2, 2026-06-06." in methodology
    assert "Day 2 (peak). Kulmi / The peak. 6 qershor / 6 June." in by_section["events"].text


def test_participation_parser_fails_loudly_on_a_missing_anchor(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    data = repository / "data/participation.ts"
    data.parent.mkdir(parents=True)
    data.write_text(
        "const yt = (id: string) => `https://example.org/${id}`;\n"
        "export const participation: ParticipationDay[] = [\n"
        '  { day: 1, date: "2026-05-31", saturday: false, peak: 1, mean: 1, median: 1,\n'
        '    source: yt("a"), note: { sq: "Një", en: "One" } },\n'
        "];\n",
        encoding="utf-8",
    )
    source = resolved_source(
        "diaspora-zbarkon",
        repository,
        [SourceRule(parser="participation_ts", include=["data/participation.ts"])],
        "https://www.diaspora-zbarkon.com/",
    )

    documents, issues, _ = parse_sources([source])

    assert documents == []
    assert len(issues) == 1
    assert "NORMALIZATION" in issues[0].reason


def test_draft_markdown_is_skipped(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    article = repository / "src/content/blog/draft/index.md"
    article.parent.mkdir(parents=True)
    article.write_text(
        "---\ntitle: Draft\ndraft: true\n---\nThis draft has enough text but must not ship.",
        encoding="utf-8",
    )
    source = resolved_source(
        "flamingo-revolution",
        repository,
        [SourceRule(parser="markdown", include=["src/content/**/*.md"])],
        "https://www.flamingorevolution.eu/",
    )

    documents, issues, discovered = parse_sources([source])

    assert discovered == 1
    assert documents == []
    assert len(issues) == 1
    assert issues[0].reason == "No meaningful public text was extracted"
