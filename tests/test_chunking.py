from flamingo_bot.chunking import ChunkingConfig, SemanticChunker
from flamingo_bot.models import SourceDocument


def make_document(text: str) -> SourceDocument:
    return SourceDocument(
        document_id="document-123456",
        source_id="flamingo-dossier",
        source_label="Flamingo Dossier",
        repository_path="/tmp/dossier",
        relative_path="data/dosje.csv",
        revision="a" * 40,
        canonical_url="https://www.flamingorevolution.eu/dosjet/",
        language="sq",
        content_type="dossier",
        title="A public-interest dossier",
        text=text,
        sticky_context=(
            "Dossier: A public-interest dossier\n"
            "Legal or institutional status: active investigation\n"
            "Allegations are not final judgments."
        ),
        legal_status="active investigation",
    )


def test_chunks_are_deterministic_bounded_and_repeat_legal_context() -> None:
    paragraphs = [
        " ".join(f"Evidence sentence {index} contains documented context." for index in range(30))
        for _ in range(8)
    ]
    document = make_document("\n\n".join(paragraphs))
    chunker = SemanticChunker(ChunkingConfig(target_tokens=180, max_tokens=220, overlap_tokens=25))

    first = chunker.chunk_document(document)
    second = chunker.chunk_document(document)

    assert len(first) > 1
    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
    assert all(item.token_count <= 220 for item in first)
    assert all("Allegations are not final judgments" in item.text for item in first)
    assert all(item.legal_status == "active investigation" for item in first)


def test_changed_text_changes_chunk_identity() -> None:
    chunker = SemanticChunker()

    first = chunker.chunk_document(make_document("A documented paragraph with sufficient text."))
    changed = chunker.chunk_document(
        make_document("A documented paragraph with different and sufficient text.")
    )

    assert first[0].chunk_id != changed[0].chunk_id
    assert first[0].content_hash != changed[0].content_hash


def test_overlap_budget_never_discards_new_segment_content() -> None:
    words = [f"unique-{index:04d}" for index in range(900)]
    document = make_document(" ".join(words))
    chunker = SemanticChunker(ChunkingConfig(target_tokens=180, max_tokens=220, overlap_tokens=25))

    chunks = chunker.chunk_document(document)
    observed = {word for chunk in chunks for word in chunk.text.split()}

    assert all(word in observed for word in words)
    assert all(chunk.token_count <= 220 for chunk in chunks)


def test_unbroken_text_is_split_without_exceeding_budget() -> None:
    document = make_document("x" * 2_000)
    chunker = SemanticChunker(ChunkingConfig(target_tokens=180, max_tokens=220, overlap_tokens=25))

    chunks = chunker.chunk_document(document)

    assert len(chunks) > 1
    assert sum(chunk.text.count("x") for chunk in chunks) >= 2_000
    assert all(chunk.token_count <= 220 for chunk in chunks)
