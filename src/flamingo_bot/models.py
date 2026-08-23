"""Typed domain models shared by ingestion, retrieval, and the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SourceDocument(BaseModel):
    document_id: str = Field(min_length=8)
    source_id: str
    source_label: str
    repository_path: str
    relative_path: str
    revision: str
    canonical_url: str
    language: str
    content_type: str
    title: str
    text: str
    sticky_context: str = ""
    legal_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source document text cannot be blank")
        return normalized


class ChunkRecord(BaseModel):
    chunk_id: str
    document_id: str
    source_id: str
    source_label: str
    repository_path: str
    relative_path: str
    revision: str
    canonical_url: str
    language: str
    content_type: str
    title: str
    text: str
    legal_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    token_count: int = Field(gt=0)
    chunk_index: int = Field(ge=0)
    embedding: list[float] | None = None


class RetrievedChunk(BaseModel):
    chunk_id: str
    source_id: str
    source_label: str
    title: str
    text: str
    canonical_url: str
    legal_status: str | None = None
    distance: float = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    id: str
    title: str
    url: str
    source: str


class ConversationTurn(BaseModel):
    """One earlier message replayed by the client.

    The service keeps no conversation state, so every turn arrives from the
    visitor's browser and is treated as unverified context rather than evidence.
    """

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=2000)


class GenerationManifest(BaseModel):
    generation_id: str
    status: Literal["staging", "active", "failed", "superseded"]
    created_at: datetime
    source_revisions: dict[str, str]
    parser_version: str
    chunker_version: str
    normalization_version: str
    embedding_deployment: str
    embedding_dimensions: int
    chunk_count: int
    document_count: int
    discovered_file_count: int
    skipped_file_count: int
    failed_file_count: int
    reused_embedding_count: int
    embedded_chunk_count: int
    removed_chunk_count: int
    source_document_counts: dict[str, int]
    validation_results: dict[str, bool]


class EmbeddingReuseContract(BaseModel):
    embedding_deployment: str
    embedding_dimensions: int
    parser_version: str
    chunker_version: str
    normalization_version: str


class ActiveGenerationSnapshot(BaseModel):
    generation_id: str | None = None
    reuse_allowed: bool = False
    content_hash_counts: dict[str, int] = Field(default_factory=dict)
    embeddings: dict[str, list[float]] = Field(default_factory=dict)


class IngestionCounts(BaseModel):
    discovered_files: int = 0
    accepted_documents: int = 0
    skipped_files: int = 0
    chunks: int = 0
    new_chunks: int = 0
    reused_embeddings: int = 0
    embedded_chunks: int = 0
    removed_chunks: int = 0
    failed_files: int = 0


class IngestionIssue(BaseModel):
    source_id: str
    relative_path: str
    reason: str


class IngestionReport(BaseModel):
    mode: Literal["dry-run", "publish"]
    started_at: datetime
    completed_at: datetime
    counts: IngestionCounts
    source_revisions: dict[str, str]
    issues: list[IngestionIssue] = Field(default_factory=list)
    generation_id: str | None = None
    active_generation_id: str | None = None
    change_set_basis: Literal["active-generation", "no-active-generation", "local-source-only"]
    published: bool = False


class RollbackReport(BaseModel):
    rollback_id: str
    previous_generation_id: str
    active_generation_id: str
    completed_at: datetime
