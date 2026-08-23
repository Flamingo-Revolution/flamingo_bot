"""In-memory provider fakes for deterministic, non-billable tests."""

from __future__ import annotations

import hashlib
import math
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from flamingo_bot.models import RetrievedChunk


class HashEmbedder:
    """Small deterministic embedder intended only for tests."""

    def __init__(self, dimensions: int = 16) -> None:
        self.dimensions = dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in text.lower().split():
                digest = hashlib.sha256(word.encode()).digest()
                vector[digest[0] % self.dimensions] += 1.0
            magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / magnitude for value in vector])
        return vectors


class InMemoryVectorStore:
    def __init__(
        self,
        records: Sequence[tuple[RetrievedChunk, Sequence[float]]] = (),
    ) -> None:
        self.records = [(record, list(vector)) for record, vector in records]

    async def status(self) -> dict[str, object]:
        return {
            "ready": bool(self.records),
            "database_id": "in-memory-test-fake",
            "active_generation": "test" if self.records else None,
            "chunk_count": len(self.records),
            "created_at": datetime(2026, 8, 23, tzinfo=UTC),
        }

    async def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        distance_threshold: float,
        source_id: str | None = None,
    ) -> list[RetrievedChunk]:
        def cosine_distance(vector: Sequence[float]) -> float:
            dot = sum(left * right for left, right in zip(query_vector, vector, strict=True))
            left = math.sqrt(sum(value * value for value in query_vector))
            right = math.sqrt(sum(value * value for value in vector))
            if left == 0 or right == 0:
                return 1.0
            return 1.0 - dot / (left * right)

        ranked = [
            record.model_copy(update={"distance": cosine_distance(vector)})
            for record, vector in self.records
            if source_id is None or record.source_id == source_id
        ]
        return sorted(
            (record for record in ranked if record.distance <= distance_threshold),
            key=lambda record: record.distance,
        )[:limit]


class ScriptedCondenser:
    """Deterministic stand-in for the Azure query-condensation call."""

    def __init__(self, rewritten: str = "", *, error: Exception | None = None) -> None:
        self.rewritten = rewritten
        self.error = error
        self.calls: list[dict[str, str | None]] = []

    async def complete_text(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> str:
        self.calls.append(
            {
                "instructions": instructions,
                "prompt": prompt,
                "safety_identifier": safety_identifier,
            }
        )
        if self.error is not None:
            raise self.error
        return self.rewritten


class ScriptedAnswerGenerator:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict[str, str | None]] = []

    async def stream_answer(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> AsyncIterator[str]:
        self.calls.append(
            {
                "instructions": instructions,
                "prompt": prompt,
                "safety_identifier": safety_identifier,
            }
        )
        for word in self.answer.split(" "):
            yield word + " "
