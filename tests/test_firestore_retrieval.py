from types import SimpleNamespace
from typing import Any

import pytest

from flamingo_bot.config import Settings
from flamingo_bot.planning import SearchPlan
from flamingo_bot.providers.firestore import FirestoreVectorStore


class Query:
    def __init__(self, client: Any, conditions: tuple[Any, ...] = (), limit: int = 32) -> None:
        self.client = client
        self.conditions = conditions
        self.maximum = limit

    def where(self, *, filter: Any) -> "Query":
        return Query(self.client, (*self.conditions, filter), self.maximum)

    def limit(self, maximum: int) -> "Query":
        return Query(self.client, self.conditions, maximum)

    def get(self, *, timeout: float, retry: Any) -> list[Any]:
        self.client.lookups.append((self.conditions, self.maximum, timeout, retry))
        return [
            SimpleNamespace(id=str(index), to_dict=lambda value=value: value)
            for index, value in enumerate(self.client.records[: self.maximum])
        ]


class Client:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.generations: list[str] = []
        self.lookups: list[Any] = []

    def collection(self, name: str) -> Any:
        if name == "rag_meta":
            return SimpleNamespace(document=lambda value: object())
        assert name == "rag_generations"

        def document(generation: str) -> Any:
            self.generations.append(generation)
            return SimpleNamespace(collection=lambda name: Query(self))

        return SimpleNamespace(document=document)


def payload(**updates: Any) -> dict[str, Any]:
    return {
        "source_id": "diaspora-zbarkon",
        "source_label": "Diaspora Zbarkon",
        "title": "Index",
        "text": "Computed global peak 100 on 20 June.",
        "canonical_url": "https://example.org/pulsi",
        "embedding": [0.0, 1.0],
        "metadata": {"section": "index"},
        "chunk_index": 0,
        **updates,
    }


@pytest.mark.asyncio
async def test_metadata_reads_are_pinned_bounded_and_use_real_cosine_distances() -> None:
    client = Client([payload()])
    store = FirestoreVectorStore(Settings(_env_file=None), client=client)  # type: ignore[arg-type]
    plan = SearchPlan.fallback("Peak").model_copy(update={"participation_section": "index"})
    chunks = await store.metadata_passages(plan, [[1.0, 0.0]], generation_id="generation-one")
    assert client.generations == ["generation-one"]
    assert len(client.lookups) == 1
    assert client.lookups[0][1] == 32
    assert 0 < client.lookups[0][2] <= 10
    assert client.lookups[0][3] is None
    assert chunks[0].distance == 1.0
    assert chunks[0].metadata["retrieval_method"] == "metadata-anchor"
    assert "embedding" not in chunks[0].model_dump()


@pytest.mark.asyncio
async def test_partial_index_slices_missing_vectors_and_unapproved_sources_are_not_anchors() -> (
    None
):
    client = Client(
        [
            payload(chunk_index=1),
            payload(embedding=None),
            payload(source_id="unapproved"),
            payload(),
        ]
    )
    store = FirestoreVectorStore(Settings(_env_file=None), client=client)  # type: ignore[arg-type]
    plan = SearchPlan.fallback("Peak").model_copy(update={"participation_section": "index"})
    chunks = await store.metadata_passages(plan, [[1.0, 0.0]], generation_id="generation-one")
    assert [chunk.chunk_id for chunk in chunks] == ["3"]


@pytest.mark.asyncio
async def test_metadata_lookup_query_and_result_budgets_are_hard_limits() -> None:
    client = Client([payload(title=str(index), metadata={}) for index in range(50)])
    store = FirestoreVectorStore(Settings(_env_file=None), client=client)  # type: ignore[arg-type]
    plan = SearchPlan.fallback("Question").model_copy(
        update={
            "publisher_prefixes": ["Reuters", "AP", "BBC"],
            "protest_days": [1, 2, 3],
            "dates": ["2026-10-13", "2026-10-14", "2026-10-15"],
            "map_aggregate": True,
        }
    )
    chunks = await store.metadata_passages(plan, [[1.0, 0.0]], generation_id="generation-one")
    assert len(client.lookups) == 8
    assert all(lookup[1] == 32 for lookup in client.lookups)
    assert len(chunks) == 16
