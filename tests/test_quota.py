from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from flamingo_bot.config import Settings
from flamingo_bot.providers import firestore as firestore_module
from flamingo_bot.providers.firestore import QUOTA_COLLECTION, FirestoreRequestQuota
from flamingo_bot.quota import InMemoryRequestQuota, QuotaWindow

HOUR = QuotaWindow(limit=10, window_seconds=3_600)
DAY = QuotaWindow(limit=100, window_seconds=86_400)


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_hourly_window_is_aligned_to_the_utc_hour() -> None:
    # 2026-08-23T12:34:56Z and 2026-08-23T12:00:00Z share an hour, 13:00 does not.
    assert HOUR.bucket(1_787_488_496.0) == HOUR.bucket(1_787_486_400.0)
    assert HOUR.bucket(1_787_490_000.0) != HOUR.bucket(1_787_486_400.0)


def test_daily_window_is_aligned_to_utc_midnight() -> None:
    assert DAY.bucket(1_787_486_400.0) == DAY.bucket(1_787_529_599.0)
    assert DAY.bucket(1_787_529_600.0) != DAY.bucket(1_787_486_400.0)


def test_retry_after_never_advises_an_immediate_retry() -> None:
    """A caller told to wait zero seconds retries straight into the same refusal."""
    for offset in (0.0, 1.0, 3_599.0, 3_599.999):
        assert HOUR.seconds_until_reset(1_787_486_400.0 + offset) >= 1


def test_visitor_allowance_is_spent_and_then_restored_by_the_next_window() -> None:
    async def scenario() -> None:
        clock = Clock(1_787_486_400.0)
        quota = InMemoryRequestQuota(
            QuotaWindow(limit=2, window_seconds=3_600), DAY, time_source=clock
        )

        assert (await quota.consume("visitor")).allowed
        assert (await quota.consume("visitor")).allowed
        refused = await quota.consume("visitor")
        assert not refused.allowed
        assert refused.scope == "client"
        assert refused.retry_after_seconds == 3_601

        clock.now += 3_600
        assert (await quota.consume("visitor")).allowed

    asyncio.run(scenario())


def test_daily_budget_is_shared_by_every_visitor() -> None:
    async def scenario() -> None:
        quota = InMemoryRequestQuota(HOUR, QuotaWindow(limit=2, window_seconds=86_400))

        assert (await quota.consume("first")).allowed
        assert (await quota.consume("second")).allowed
        refused = await quota.consume("third")

        assert not refused.allowed
        assert refused.scope == "daily"

    asyncio.run(scenario())


def test_a_refused_request_spends_neither_allowance() -> None:
    """The two counters move together or not at all.

    Otherwise a visitor is billed for a request the daily budget rejected, and a
    visitor who is already out silently drains the budget everyone else shares.
    """

    async def scenario() -> None:
        quota = InMemoryRequestQuota(
            QuotaWindow(limit=1, window_seconds=3_600),
            QuotaWindow(limit=3, window_seconds=86_400),
        )

        await quota.consume("heavy")
        for _ in range(5):
            assert not (await quota.consume("heavy")).allowed

        # Two of the three daily requests remain for everyone else.
        assert (await quota.consume("second")).allowed
        assert (await quota.consume("third")).allowed
        assert not (await quota.consume("fourth")).allowed

    asyncio.run(scenario())


class FakeSnapshot:
    def __init__(self, value: dict[str, Any] | None) -> None:
        self.exists = value is not None
        self._value = deepcopy(value)

    def to_dict(self) -> dict[str, Any] | None:
        return deepcopy(self._value)


class FakeDocument:
    def __init__(self, client: FakeClient, path: str) -> None:
        self.client = client
        self.path = path

    def get(self, **kwargs: Any) -> FakeSnapshot:
        del kwargs
        self.client.operations.append(("read", self.path))
        return FakeSnapshot(self.client.documents.get(self.path))

    def set(self, value: dict[str, Any]) -> None:
        self.client.documents[self.path] = deepcopy(value)


class FakeCollection:
    def __init__(self, client: FakeClient, path: str) -> None:
        self.client = client
        self.path = path

    def document(self, document_id: str) -> FakeDocument:
        return FakeDocument(self.client, f"{self.path}/{document_id}")


class FakeTransaction:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def set(self, reference: FakeDocument, value: dict[str, Any]) -> None:
        self.client.operations.append(("write", reference.path))
        reference.set(value)


class FakeClient:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.operations: list[tuple[str, str]] = []

    def collection(self, collection_id: str) -> FakeCollection:
        return FakeCollection(self, collection_id)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def close(self) -> None:
        pass


@pytest.fixture
def firestore_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FirestoreRequestQuota, FakeClient, Clock]:
    monkeypatch.setattr(firestore_module.firestore_v1, "transactional", lambda function: function)
    client = FakeClient()
    clock = Clock(1_787_486_400.0)
    quota = FirestoreRequestQuota(
        Settings(_env_file=None, gcp_project_id="test-project"),
        QuotaWindow(limit=2, window_seconds=3_600),
        QuotaWindow(limit=3, window_seconds=86_400),
        client=client,  # type: ignore[arg-type]
        time_source=clock,
    )
    return quota, client, clock


def test_firestore_quota_counts_are_shared_state_not_process_state(
    firestore_quota: tuple[FirestoreRequestQuota, FakeClient, Clock],
) -> None:
    """Two instances pointed at one database must see one count, not one each."""
    quota, client, clock = firestore_quota
    second_instance = FirestoreRequestQuota(
        Settings(_env_file=None, gcp_project_id="test-project"),
        quota.client_window,
        quota.daily_window,
        client=client,  # type: ignore[arg-type]
        time_source=clock,
    )

    async def scenario() -> None:
        assert (await quota.consume("visitor")).allowed
        assert (await second_instance.consume("visitor")).allowed
        refused = await second_instance.consume("visitor")
        assert not refused.allowed
        assert refused.scope == "client"

    asyncio.run(scenario())


def test_firestore_quota_reads_both_counters_before_writing_either(
    firestore_quota: tuple[FirestoreRequestQuota, FakeClient, Clock],
) -> None:
    """Firestore rejects a transaction that writes before it reads."""
    quota, client, _ = firestore_quota

    asyncio.run(quota.consume("visitor"))

    kinds = [kind for kind, _ in client.operations]
    assert kinds == ["read", "read", "write", "write"]


def test_firestore_quota_documents_carry_a_ttl_field(
    firestore_quota: tuple[FirestoreRequestQuota, FakeClient, Clock],
) -> None:
    quota, client, _ = firestore_quota

    asyncio.run(quota.consume("visitor"))

    for path, value in client.documents.items():
        assert path.startswith(f"{QUOTA_COLLECTION}/")
        assert value["count"] == 1
        assert value["expires_at"] > value["window_start"]


def test_firestore_quota_ignores_a_corrupted_counter(
    firestore_quota: tuple[FirestoreRequestQuota, FakeClient, Clock],
) -> None:
    """A malformed document must not be readable as free requests."""
    quota, client, _ = firestore_quota
    asyncio.run(quota.consume("visitor"))
    for path in client.documents:
        client.documents[path]["count"] = "not-a-number"

    asyncio.run(quota.consume("visitor"))

    for value in client.documents.values():
        assert value["count"] == 1
