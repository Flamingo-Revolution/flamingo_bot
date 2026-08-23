from datetime import UTC, date, datetime

import pytest

from flamingo_bot.config import Settings
from flamingo_bot.errors import PublicationError
from flamingo_bot.models import ChunkRecord, EmbeddingReuseContract, GenerationManifest
from flamingo_bot.providers.firestore import (
    FirestoreVectorStore,
    firestore_credentials,
    manifest_supports_reuse,
    validate_publication_inputs,
)


def reuse_contract() -> EmbeddingReuseContract:
    return EmbeddingReuseContract(
        embedding_deployment="text-embedding-3-large",
        embedding_dimensions=1024,
        parser_version="1.0.0",
        chunker_version="1.0.0",
        normalization_version="1.0.0",
    )


def test_matching_generation_contract_allows_embedding_reuse() -> None:
    manifest = reuse_contract().model_dump()

    assert manifest_supports_reuse(manifest, reuse_contract())


def test_same_dimensions_do_not_hide_changed_embedding_contract() -> None:
    manifest = reuse_contract().model_dump()
    manifest["embedding_deployment"] = "different-deployment"

    assert not manifest_supports_reuse(manifest, reuse_contract())


def test_parser_or_chunker_change_forces_rebuild() -> None:
    manifest = reuse_contract().model_dump()
    manifest["chunker_version"] = "2.0.0"

    assert not manifest_supports_reuse(manifest, reuse_contract())


def publication_manifest() -> GenerationManifest:
    return GenerationManifest(
        generation_id="generation-test",
        status="staging",
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
        source_revisions={"source": "abc123"},
        parser_version="1.0.0",
        chunker_version="1.0.0",
        normalization_version="1.0.0",
        embedding_deployment="embedding",
        embedding_dimensions=4,
        chunk_count=1,
        document_count=1,
        discovered_file_count=1,
        skipped_file_count=0,
        failed_file_count=0,
        reused_embedding_count=0,
        embedded_chunk_count=1,
        removed_chunk_count=0,
        source_document_counts={"source": 1},
        validation_results={"source_coverage": True, "embedding_dimensions": True},
    )


def publication_chunk() -> ChunkRecord:
    return ChunkRecord(
        chunk_id="chunk-test",
        document_id="document-test",
        source_id="source",
        source_label="Source",
        repository_path="/source",
        relative_path="content.md",
        revision="abc123",
        canonical_url="https://example.org/content",
        language="sq",
        content_type="markdown",
        title="Title",
        text="Evidence text",
        content_hash="content-hash",
        token_count=3,
        chunk_index=0,
        embedding=[0.1, 0.2, 0.3, 0.4],
    )


def test_publication_input_requires_complete_audited_vectors() -> None:
    validate_publication_inputs(publication_manifest(), [publication_chunk()])

    invalid_chunk = publication_chunk().model_copy(update={"embedding": [0.1, 0.2]})
    with pytest.raises(PublicationError, match="Invalid embedding"):
        validate_publication_inputs(publication_manifest(), [invalid_chunk])

    invalid_manifest = publication_manifest().model_copy(
        update={"validation_results": {"source_coverage": False}}
    )
    with pytest.raises(PublicationError, match="source coverage"):
        validate_publication_inputs(invalid_manifest, [publication_chunk()])


def test_firestore_credentials_are_ambient_unless_impersonation_is_requested() -> None:
    assert firestore_credentials(Settings(_env_file=None)) is None


def test_chunk_payload_serializes_date_metadata_for_firestore() -> None:
    source = publication_chunk().model_copy(
        update={"metadata": {"publication_date": date(2026, 5, 31)}}
    )

    payload = FirestoreVectorStore._chunk_payload(source)

    assert payload["metadata"] == {"publication_date": "2026-05-31"}
