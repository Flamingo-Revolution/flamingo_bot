"""Provider boundaries owned by the application."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from flamingo_bot.models import (
    ActiveGenerationSnapshot,
    ChunkRecord,
    EmbeddingReuseContract,
    GenerationManifest,
    RetrievedChunk,
)
from flamingo_bot.quota import QuotaDecision


class Embedder(Protocol):
    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


class AnswerGenerator(Protocol):
    def stream_answer(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> AsyncIterator[str]: ...


class QueryCondenser(Protocol):
    async def complete_text(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> str: ...


class VectorStore(Protocol):
    async def status(self) -> dict[str, object]: ...

    async def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        distance_threshold: float,
        source_id: str | None = None,
    ) -> list[RetrievedChunk]: ...


class PublicationStore(VectorStore, Protocol):
    async def load_active_snapshot(
        self, contract: EmbeddingReuseContract
    ) -> ActiveGenerationSnapshot: ...

    async def publish_generation(
        self,
        manifest: GenerationManifest,
        chunks: Sequence[ChunkRecord],
    ) -> None: ...


class RequestQuota(Protocol):
    async def consume(self, client_key: str) -> QuotaDecision:
        """Charge one request to the visitor allowance and the service-wide budget."""
        ...


class ClosableProvider(Protocol):
    async def close(self) -> None: ...
