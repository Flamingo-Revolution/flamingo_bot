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
from flamingo_bot.planning import PassageSelection, SearchPlan
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


class QueryPlanner(Protocol):
    async def plan_search(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> SearchPlan: ...


class PassageRanker(Protocol):
    async def select_passages(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> PassageSelection: ...


class VectorStore(Protocol):
    async def status(self) -> dict[str, object]: ...

    async def active_generation(self) -> str | None: ...

    async def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        distance_threshold: float,
        source_id: str | None = None,
        generation_id: str | None = None,
    ) -> list[RetrievedChunk]: ...

    async def metadata_passages(
        self,
        plan: SearchPlan,
        vectors: Sequence[Sequence[float]],
        *,
        generation_id: str,
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
