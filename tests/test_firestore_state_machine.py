from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from flamingo_bot.config import Settings
from flamingo_bot.errors import PublicationError, RollbackError
from flamingo_bot.models import ChunkRecord, GenerationManifest
from flamingo_bot.providers import firestore as firestore_module
from flamingo_bot.providers.firestore import FirestoreVectorStore


def _merge_update(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if "." not in key:
            target[key] = value
            continue
        parent, child = key.split(".", 1)
        nested = target.setdefault(parent, {})
        assert isinstance(nested, dict)
        nested[child] = value


class FakeSnapshot:
    def __init__(self, path: str, value: dict[str, Any] | None) -> None:
        self.id = path.rsplit("/", 1)[-1]
        self.exists = value is not None
        self._value = deepcopy(value)

    def to_dict(self) -> dict[str, Any] | None:
        return deepcopy(self._value)


class FakeQuery:
    def __init__(self, client: FakeClient, collection_path: str) -> None:
        self.client = client
        self.collection_path = collection_path

    def stream(self, timeout: int | None = None) -> list[FakeSnapshot]:
        del timeout
        prefix = f"{self.collection_path}/"
        return [
            FakeSnapshot(path, value)
            for path, value in sorted(self.client.documents.items())
            if path.startswith(prefix) and "/" not in path[len(prefix) :]
        ]


class FakeVectorQuery(FakeQuery):
    def get(self, timeout: int | None = None) -> list[FakeSnapshot]:
        if not self.client.vector_smoke_succeeds:
            return []
        return self.stream(timeout)[:1]


class FakeCollection:
    def __init__(self, client: FakeClient, path: str) -> None:
        self.client = client
        self.path = path

    def document(self, document_id: str) -> FakeDocument:
        return FakeDocument(self.client, f"{self.path}/{document_id}")

    def select(self, fields: list[str]) -> FakeQuery:
        del fields
        return FakeQuery(self.client, self.path)

    def find_nearest(self, **kwargs: Any) -> FakeVectorQuery:
        del kwargs
        return FakeVectorQuery(self.client, self.path)


class FakeDocument:
    def __init__(self, client: FakeClient, path: str) -> None:
        self.client = client
        self.path = path

    def get(self, **kwargs: Any) -> FakeSnapshot:
        del kwargs
        return FakeSnapshot(self.path, self.client.documents.get(self.path))

    def create(self, value: dict[str, Any]) -> None:
        if self.path in self.client.documents:
            raise AssertionError(f"duplicate create: {self.path}")
        self.client.documents[self.path] = deepcopy(value)

    def set(self, value: dict[str, Any]) -> None:
        self.client.documents[self.path] = deepcopy(value)

    def update(self, value: dict[str, Any], **kwargs: Any) -> None:
        del kwargs
        if self.path not in self.client.documents:
            raise AssertionError(f"update missing document: {self.path}")
        _merge_update(self.client.documents[self.path], deepcopy(value))

    def collection(self, collection_id: str) -> FakeCollection:
        return FakeCollection(self.client, f"{self.path}/{collection_id}")


class FakeBatch:
    def create(self, reference: FakeDocument, value: dict[str, Any]) -> None:
        reference.create(value)

    def commit(self, timeout: int | None = None) -> None:
        del timeout


class FakeTransaction:
    def create(self, reference: FakeDocument, value: dict[str, Any]) -> None:
        reference.create(value)

    def set(self, reference: FakeDocument, value: dict[str, Any]) -> None:
        reference.set(value)

    def update(self, reference: FakeDocument, value: dict[str, Any]) -> None:
        reference.update(value)


class FakeClient:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.vector_smoke_succeeds = True

    def collection(self, collection_id: str) -> FakeCollection:
        return FakeCollection(self, collection_id)

    def batch(self) -> FakeBatch:
        return FakeBatch()

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def close(self) -> None:
        pass


def manifest(generation_id: str) -> GenerationManifest:
    return GenerationManifest(
        generation_id=generation_id,
        status="staging",
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
        source_revisions={"source": "abc123"},
        parser_version="1.1.0",
        chunker_version="1.1.0",
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


def chunk() -> ChunkRecord:
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


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> tuple[FirestoreVectorStore, FakeClient]:
    monkeypatch.setattr(firestore_module.firestore_v1, "transactional", lambda function: function)
    client = FakeClient()
    vector_store = FirestoreVectorStore(
        Settings(gcp_project_id="test-project", firestore_database_id="flamingo-rag"),
        client=client,  # type: ignore[arg-type]
    )
    return vector_store, client


def test_publish_and_rollback_are_transactional_state_transitions(
    store: tuple[FirestoreVectorStore, FakeClient],
) -> None:
    vector_store, client = store
    vector_store._publish_generation_sync(manifest("generation-one"), [chunk()])
    vector_store._publish_generation_sync(manifest("generation-two"), [chunk()])

    assert client.documents["rag_meta/current"]["generation_id"] == "generation-two"
    assert client.documents["rag_generations/generation-one"]["status"] == "superseded"
    assert client.documents["ingestion_runs/generation-two"]["status"] == "completed"

    report = vector_store._rollback_generation_sync(None, "rollback-test-001")

    assert report.previous_generation_id == "generation-two"
    assert report.active_generation_id == "generation-one"
    assert client.documents["rag_meta/current"]["generation_id"] == "generation-one"
    assert client.documents["rag_meta/current"]["previous_generation_id"] == "generation-two"
    assert client.documents["rag_generations/generation-one"]["status"] == "active"
    assert client.documents["rag_generations/generation-two"]["status"] == "superseded"
    assert client.documents["rollback_runs/rollback-test-001"]["status"] == "completed"


def test_failed_staging_never_changes_active_pointer(
    store: tuple[FirestoreVectorStore, FakeClient],
) -> None:
    vector_store, client = store
    client.vector_smoke_succeeds = False

    with pytest.raises(PublicationError, match="vector smoke test"):
        vector_store._publish_generation_sync(manifest("generation-failed"), [chunk()])

    assert "rag_meta/current" not in client.documents
    assert client.documents["rag_generations/generation-failed"]["status"] == "failed"
    assert client.documents["ingestion_runs/generation-failed"]["status"] == "failed"


def test_client_side_serialization_failure_marks_staging_failed(
    store: tuple[FirestoreVectorStore, FakeClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_store, client = store

    def fail_serialization(_: ChunkRecord) -> dict[str, Any]:
        raise TypeError("unsupported metadata value")

    monkeypatch.setattr(vector_store, "_chunk_payload", fail_serialization)

    with pytest.raises(PublicationError, match="publication failed"):
        vector_store._publish_generation_sync(manifest("generation-serialization"), [chunk()])

    assert "rag_meta/current" not in client.documents
    assert client.documents["rag_generations/generation-serialization"]["status"] == "failed"
    assert client.documents["ingestion_runs/generation-serialization"]["status"] == "failed"


def test_rollback_revalidates_retained_chunk_count(
    store: tuple[FirestoreVectorStore, FakeClient],
) -> None:
    vector_store, client = store
    vector_store._publish_generation_sync(manifest("generation-one"), [chunk()])
    vector_store._publish_generation_sync(manifest("generation-two"), [chunk()])
    del client.documents["rag_generations/generation-one/chunks/chunk-test"]

    with pytest.raises(RollbackError, match="has 0 chunks; expected 1"):
        vector_store._rollback_generation_sync(None, "rollback-test-002")

    assert client.documents["rag_meta/current"]["generation_id"] == "generation-two"
    assert "rollback_runs/rollback-test-002" not in client.documents
