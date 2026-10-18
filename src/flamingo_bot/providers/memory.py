"""In-memory provider fakes for deterministic, non-billable tests."""

from __future__ import annotations

import hashlib
import math
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from flamingo_bot.models import RetrievedChunk
from flamingo_bot.planning import (
    MAX_ANCHORS,
    MAX_METADATA_MATCHES,
    PassageSelection,
    SearchPlan,
    cosine_distance,
    deduplicate,
    metadata_filters,
)


def _matches_metadata(
    record: RetrievedChunk, conditions: Sequence[tuple[str, str, str | int]]
) -> bool:
    for field, operation, expected in conditions:
        actual = (
            record.metadata.get(field.removeprefix("metadata."))
            if field.startswith("metadata.")
            else record.model_dump().get(field, record.metadata.get(field))
        )
        if operation == "==":
            if actual != expected:
                return False
        elif not isinstance(actual, str) or not isinstance(expected, str):
            return False
        elif operation == ">=" and actual < expected:
            return False
        elif operation == "<" and actual >= expected:
            return False
    return True


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

    async def active_generation(self) -> str | None:
        return "test" if self.records else None

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
        generation_id: str | None = None,
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

    async def metadata_passages(
        self,
        plan: SearchPlan,
        vectors: Sequence[Sequence[float]],
        *,
        generation_id: str,
    ) -> list[RetrievedChunk]:
        found: dict[str, RetrievedChunk] = {}
        for conditions in metadata_filters(plan):
            matches = 0
            for record, vector in self.records:
                if not _matches_metadata(record, conditions):
                    continue
                matches += 1
                if matches > MAX_METADATA_MATCHES:
                    break
                if (
                    record.metadata.get("section") == "index"
                    and record.metadata.get("chunk_index", 0) != 0
                ):
                    continue
                found[record.chunk_id] = record.model_copy(
                    update={
                        "distance": min(cosine_distance(vector, item) for item in vectors),
                        "metadata": {**record.metadata, "retrieval_method": "metadata-anchor"},
                    }
                )
        return deduplicate(list(found.values()))[:MAX_ANCHORS]


class ScriptedPlanner:
    """Deterministic stand-in for typed Azure query planning."""

    def __init__(self, plan: SearchPlan | None = None, *, error: Exception | None = None) -> None:
        self.plan = plan or SearchPlan.fallback("Revolucioni Flamingo")
        self.error = error
        self.calls: list[dict[str, str | None]] = []

    async def plan_search(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> SearchPlan:
        self.calls.append(
            {
                "instructions": instructions,
                "prompt": prompt,
                "safety_identifier": safety_identifier,
            }
        )
        if self.error is not None:
            raise self.error
        return self.plan


class ScriptedRanker:
    def __init__(self, selected_ids: Sequence[str] = (), *, error: Exception | None = None) -> None:
        self.selected_ids = list(selected_ids)
        self.error = error
        self.calls: list[dict[str, str | None]] = []

    async def select_passages(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> PassageSelection:
        self.calls.append(
            {"instructions": instructions, "prompt": prompt, "safety_identifier": safety_identifier}
        )
        if self.error is not None:
            raise self.error
        return PassageSelection(selected_ids=self.selected_ids)


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
