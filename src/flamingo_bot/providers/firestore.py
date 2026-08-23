"""Firestore Vector Search storage with staged generation publication."""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import google.auth
from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import firestore_v1
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

from flamingo_bot.config import Settings
from flamingo_bot.errors import ConfigurationError, ProviderError, PublicationError, RollbackError
from flamingo_bot.models import (
    ActiveGenerationSnapshot,
    ChunkRecord,
    EmbeddingReuseContract,
    GenerationManifest,
    RetrievedChunk,
    RollbackReport,
)
from flamingo_bot.quota import ALLOWED, QuotaDecision, QuotaWindow

GENERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
QUOTA_COLLECTION = "request_quotas"


def validate_generation_id(generation_id: str) -> str:
    normalized = generation_id.strip()
    if not GENERATION_ID_PATTERN.fullmatch(normalized):
        raise RollbackError("Generation ID contains invalid characters or has an invalid length")
    return normalized


def manifest_supports_reuse(manifest: dict[str, Any], contract: EmbeddingReuseContract) -> bool:
    return all(manifest.get(field) == expected for field, expected in contract.model_dump().items())


def validate_publication_inputs(
    manifest: GenerationManifest, chunks: Sequence[ChunkRecord]
) -> None:
    if manifest.status != "staging":
        raise PublicationError("A new generation must begin in staging status")
    if manifest.chunk_count != len(chunks):
        raise PublicationError("Manifest chunk count does not match the staged chunks")
    if not manifest.source_document_counts or any(
        count <= 0 for count in manifest.source_document_counts.values()
    ):
        raise PublicationError("Manifest source coverage is incomplete")
    if not manifest.validation_results.get("source_coverage"):
        raise PublicationError("Manifest source coverage was not validated")
    if not manifest.validation_results.get("embedding_dimensions"):
        raise PublicationError("Manifest embedding dimensions were not validated")
    expected_dimensions = manifest.embedding_dimensions
    for chunk in chunks:
        if chunk.embedding is None or len(chunk.embedding) != expected_dimensions:
            raise PublicationError(f"Invalid embedding for chunk {chunk.chunk_id}")


def firestore_credentials(settings: Settings) -> Credentials | None:
    target_principal = settings.gcp_impersonate_service_account
    if not target_principal:
        return None
    try:
        source_credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    except DefaultCredentialsError as exc:
        raise ConfigurationError(
            "Google Application Default Credentials are required for service-account impersonation"
        ) from exc
    return impersonated_credentials.Credentials(  # type: ignore[no-untyped-call]
        source_credentials=source_credentials,
        target_principal=target_principal,
        target_scopes=[CLOUD_PLATFORM_SCOPE],
        lifetime=3600,
        quota_project_id=settings.gcp_project_id,
    )


def _counter_value(snapshot: Any) -> int:
    """Read a counter defensively; a malformed document must not grant free requests."""
    if not snapshot.exists:
        return 0
    value = (snapshot.to_dict() or {}).get("count")
    return value if isinstance(value, int) and value > 0 else 0


class FirestoreRequestQuota:
    """Request quotas held in Firestore so every Cloud Run instance shares one count.

    Each fixed window is one document holding one counter, and both counters are
    read and written inside a single transaction. That is what makes the pair
    consistent: a request refused by the daily budget never draws down the
    visitor's hourly allowance, and a visitor who is already out never draws down
    the shared budget.
    """

    def __init__(
        self,
        settings: Settings,
        client_window: QuotaWindow,
        daily_window: QuotaWindow,
        *,
        client: firestore_v1.Client | None = None,
        time_source: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.client_window = client_window
        self.daily_window = daily_window
        self._time_source = time_source
        if client is not None:
            self.client = client
        else:
            try:
                self.client = firestore_v1.Client(
                    project=settings.gcp_project_id,
                    database=settings.firestore_database_id,
                    credentials=firestore_credentials(settings),
                )
            except DefaultCredentialsError as exc:
                raise ConfigurationError(
                    "Google Application Default Credentials are required for Firestore access"
                ) from exc
        self.collection = self.client.collection(QUOTA_COLLECTION)

    @staticmethod
    def _payload(count: int, bucket: int, window: QuotaWindow) -> dict[str, Any]:
        window_start = bucket * window.window_seconds
        return {
            "count": count,
            "limit": window.limit,
            "window_seconds": window.window_seconds,
            "window_start": datetime.fromtimestamp(window_start, UTC),
            # One spare window of slack before a Firestore TTL policy on this field
            # removes the document. Nothing reads a closed window.
            "expires_at": datetime.fromtimestamp(window_start + window.window_seconds * 2, UTC),
        }

    def _consume_sync(self, client_key: str) -> QuotaDecision:
        now = self._time_source()
        client_bucket = self.client_window.bucket(now)
        daily_bucket = self.daily_window.bucket(now)
        client_ref = self.collection.document(
            f"client-{client_key}-{self.client_window.window_seconds}-{client_bucket}"
        )
        daily_ref = self.collection.document(
            f"daily-{self.daily_window.window_seconds}-{daily_bucket}"
        )

        @firestore_v1.transactional
        def charge(transaction: firestore_v1.Transaction) -> QuotaDecision:
            # Firestore requires every read in a transaction to precede its writes.
            client_snapshot = client_ref.get(transaction=transaction)
            daily_snapshot = daily_ref.get(transaction=transaction)
            client_count = _counter_value(client_snapshot)
            if client_count >= self.client_window.limit:
                return QuotaDecision(
                    allowed=False,
                    scope="client",
                    retry_after_seconds=self.client_window.seconds_until_reset(now),
                )
            daily_count = _counter_value(daily_snapshot)
            if daily_count >= self.daily_window.limit:
                return QuotaDecision(
                    allowed=False,
                    scope="daily",
                    retry_after_seconds=self.daily_window.seconds_until_reset(now),
                )
            transaction.set(
                client_ref, self._payload(client_count + 1, client_bucket, self.client_window)
            )
            transaction.set(
                daily_ref, self._payload(daily_count + 1, daily_bucket, self.daily_window)
            )
            return ALLOWED

        decision: QuotaDecision = charge(self.client.transaction())
        return decision

    async def consume(self, client_key: str) -> QuotaDecision:
        try:
            return await asyncio.to_thread(self._consume_sync, client_key)
        except GoogleAPICallError as exc:
            raise ProviderError("Firestore request quota check failed") from exc

    async def close(self) -> None:
        await asyncio.to_thread(self.client.close)


class FirestoreVectorStore:
    def __init__(self, settings: Settings, client: firestore_v1.Client | None = None) -> None:
        self.settings = settings
        if client is not None:
            self.client = client
        else:
            try:
                self.client = firestore_v1.Client(
                    project=settings.gcp_project_id,
                    database=settings.firestore_database_id,
                    credentials=firestore_credentials(settings),
                )
            except DefaultCredentialsError as exc:
                raise ConfigurationError(
                    "Google Application Default Credentials are required for Firestore access"
                ) from exc
        self.current_ref = self.client.collection("rag_meta").document("current")

    def _active_generation_id(self) -> str | None:
        snapshot = self.current_ref.get(timeout=10)
        if not snapshot.exists:
            return None
        value = snapshot.to_dict() or {}
        generation_id = value.get("generation_id")
        return str(generation_id) if generation_id else None

    def _status_sync(self) -> dict[str, object]:
        generation_id = self._active_generation_id()
        if generation_id is None:
            return {
                "ready": False,
                "database_id": self.settings.firestore_database_id,
                "active_generation": None,
            }
        manifest = self.client.collection("rag_generations").document(generation_id).get(timeout=10)
        data = manifest.to_dict() or {}
        return {
            "ready": manifest.exists and data.get("status") == "active",
            "database_id": self.settings.firestore_database_id,
            "active_generation": generation_id,
            "chunk_count": data.get("chunk_count"),
            "created_at": data.get("created_at"),
        }

    async def status(self) -> dict[str, object]:
        try:
            return await asyncio.to_thread(self._status_sync)
        except GoogleAPICallError as exc:
            raise ProviderError("Firestore status check failed") from exc

    def _search_sync(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        distance_threshold: float,
        source_id: str | None = None,
    ) -> list[RetrievedChunk]:
        generation_id = self._active_generation_id()
        if generation_id is None:
            return []
        collection = (
            self.client.collection("rag_generations").document(generation_id).collection("chunks")
        )
        query = (
            collection.where(filter=FieldFilter("source_id", "==", source_id))
            if source_id
            else collection
        )
        vector_query = query.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_vector),
            limit=limit,
            distance_measure=DistanceMeasure.COSINE,
            distance_result_field="vector_distance",
            distance_threshold=distance_threshold,
        )
        results: list[RetrievedChunk] = []
        for snapshot in vector_query.get(timeout=20):
            data = snapshot.to_dict() or {}
            results.append(
                RetrievedChunk(
                    chunk_id=snapshot.id,
                    source_id=str(data["source_id"]),
                    source_label=str(data["source_label"]),
                    title=str(data["title"]),
                    text=str(data["text"]),
                    canonical_url=str(data["canonical_url"]),
                    legal_status=(str(data["legal_status"]) if data.get("legal_status") else None),
                    distance=float(data["vector_distance"]),
                    metadata=dict(data.get("metadata") or {}),
                )
            )
        return results

    async def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        distance_threshold: float,
        source_id: str | None = None,
    ) -> list[RetrievedChunk]:
        try:
            return await asyncio.to_thread(
                self._search_sync,
                query_vector,
                limit=limit,
                distance_threshold=distance_threshold,
                source_id=source_id,
            )
        except GoogleAPICallError as exc:
            raise ProviderError("Firestore vector search failed") from exc

    def _load_active_snapshot_sync(
        self, contract: EmbeddingReuseContract
    ) -> ActiveGenerationSnapshot:
        generation_id = self._active_generation_id()
        if generation_id is None:
            return ActiveGenerationSnapshot()
        generation_ref = self.client.collection("rag_generations").document(generation_id)
        manifest = generation_ref.get(timeout=10)
        manifest_data = manifest.to_dict() or {}
        if not manifest.exists or manifest_data.get("status") != "active":
            raise ProviderError("The active Firestore pointer references an invalid generation")
        expected_chunk_count = manifest_data.get("chunk_count")
        if not isinstance(expected_chunk_count, int) or expected_chunk_count <= 0:
            raise ProviderError("The active Firestore generation has an invalid chunk count")
        reuse_allowed = manifest_supports_reuse(manifest_data, contract)
        collection = generation_ref.collection("chunks")
        fields = ["content_hash", "embedding"] if reuse_allowed else ["content_hash"]
        content_hash_counts: Counter[str] = Counter()
        embeddings: dict[str, list[float]] = {}
        for snapshot in collection.select(fields).stream(timeout=60):
            data = snapshot.to_dict() or {}
            content_hash = data.get("content_hash")
            vector = data.get("embedding")
            if not content_hash:
                raise ProviderError("An active Firestore chunk is missing its content hash")
            normalized_hash = str(content_hash)
            content_hash_counts[normalized_hash] += 1
            if reuse_allowed:
                if vector is None:
                    raise ProviderError("An active Firestore chunk is missing its embedding")
                normalized_vector = [float(value) for value in vector]
                if len(normalized_vector) != contract.embedding_dimensions:
                    raise ProviderError("An active Firestore embedding has invalid dimensions")
                embeddings[normalized_hash] = normalized_vector
        if sum(content_hash_counts.values()) != expected_chunk_count:
            raise ProviderError("The active Firestore generation chunk count does not match")
        return ActiveGenerationSnapshot(
            generation_id=generation_id,
            reuse_allowed=reuse_allowed,
            content_hash_counts=dict(content_hash_counts),
            embeddings=embeddings,
        )

    async def load_active_snapshot(
        self, contract: EmbeddingReuseContract
    ) -> ActiveGenerationSnapshot:
        try:
            return await asyncio.to_thread(self._load_active_snapshot_sync, contract)
        except NotFound:
            return ActiveGenerationSnapshot()
        except GoogleAPICallError as exc:
            raise ProviderError("Unable to read the active Firestore generation") from exc

    @staticmethod
    def _chunk_payload(chunk: ChunkRecord) -> dict[str, Any]:
        if chunk.embedding is None:
            raise PublicationError(f"Chunk has no embedding: {chunk.chunk_id}")
        payload = chunk.model_dump(mode="json", exclude={"embedding"})
        payload["embedding"] = Vector(chunk.embedding)
        return payload

    def _publish_generation_sync(
        self,
        manifest: GenerationManifest,
        chunks: Sequence[ChunkRecord],
    ) -> None:
        validate_publication_inputs(manifest, chunks)
        generation_ref = self.client.collection("rag_generations").document(manifest.generation_id)
        run_ref = self.client.collection("ingestion_runs").document(manifest.generation_id)
        collection = generation_ref.collection("chunks")
        staging_created = False
        try:
            if generation_ref.get(timeout=10).exists:
                raise PublicationError(f"Generation already exists: {manifest.generation_id}")

            initial_batch = self.client.batch()
            initial_batch.create(generation_ref, manifest.model_dump(mode="python"))
            initial_batch.create(
                run_ref,
                {
                    "generation_id": manifest.generation_id,
                    "status": "staging",
                    "started_at": manifest.created_at,
                    "source_revisions": manifest.source_revisions,
                    "source_document_counts": manifest.source_document_counts,
                    "counts": {
                        "discovered_files": manifest.discovered_file_count,
                        "accepted_documents": manifest.document_count,
                        "skipped_files": manifest.skipped_file_count,
                        "failed_files": manifest.failed_file_count,
                        "chunks": manifest.chunk_count,
                        "reused_embeddings": manifest.reused_embedding_count,
                        "embedded_chunks": manifest.embedded_chunk_count,
                        "removed_chunks": manifest.removed_chunk_count,
                    },
                    "validation_results": manifest.validation_results,
                },
            )
            initial_batch.commit(timeout=20)
            staging_created = True

            for start in range(0, len(chunks), 400):
                batch = self.client.batch()
                for chunk in chunks[start : start + 400]:
                    batch.create(collection.document(chunk.chunk_id), self._chunk_payload(chunk))
                batch.commit(timeout=60)

            written_count = sum(1 for _ in collection.select(["chunk_id"]).stream(timeout=60))
            if written_count != manifest.chunk_count:
                generation_ref.update(
                    {
                        "status": "failed",
                        "validation_error": "chunk_count_mismatch",
                        "validation_results.chunk_count": False,
                    },
                    timeout=20,
                )
                run_ref.update(
                    {
                        "status": "failed",
                        "completed_at": firestore_v1.SERVER_TIMESTAMP,
                        "validation_error": "chunk_count_mismatch",
                        "validation_results.chunk_count": False,
                    },
                    timeout=20,
                )
                raise PublicationError(
                    f"Staged generation has {written_count} chunks; expected {manifest.chunk_count}"
                )

            representative_chunks: dict[str, ChunkRecord] = {}
            for chunk in chunks:
                representative_chunks.setdefault(chunk.source_id, chunk)
            representative_searches_passed = True
            for representative in representative_chunks.values():
                smoke_query = collection.find_nearest(
                    vector_field="embedding",
                    query_vector=Vector(representative.embedding or []),
                    limit=1,
                    distance_measure=DistanceMeasure.COSINE,
                    distance_result_field="vector_distance",
                    distance_threshold=2.0,
                )
                if not smoke_query.get(timeout=20):
                    representative_searches_passed = False
                    break
            if not representative_searches_passed:
                generation_ref.update(
                    {
                        "status": "failed",
                        "validation_error": "vector_smoke_test_failed",
                        "validation_results.chunk_count": True,
                        "validation_results.vector_smoke": False,
                        "validation_results.representative_searches": False,
                    },
                    timeout=20,
                )
                run_ref.update(
                    {
                        "status": "failed",
                        "completed_at": firestore_v1.SERVER_TIMESTAMP,
                        "validation_error": "vector_smoke_test_failed",
                        "validation_results.chunk_count": True,
                        "validation_results.vector_smoke": False,
                        "validation_results.representative_searches": False,
                    },
                    timeout=20,
                )
                raise PublicationError("Staged generation failed its vector smoke test")

            @firestore_v1.transactional
            def activate(transaction: firestore_v1.Transaction) -> None:
                current = self.current_ref.get(transaction=transaction)
                previous_id = None
                if current.exists:
                    previous_id = (current.to_dict() or {}).get("generation_id")
                transaction.set(
                    self.current_ref,
                    {
                        "generation_id": manifest.generation_id,
                        "previous_generation_id": previous_id,
                        "activated_at": firestore_v1.SERVER_TIMESTAMP,
                    },
                )
                transaction.update(
                    generation_ref,
                    {
                        "status": "active",
                        "activated_at": firestore_v1.SERVER_TIMESTAMP,
                        "completed_at": firestore_v1.SERVER_TIMESTAMP,
                        "validation_results.chunk_count": True,
                        "validation_results.vector_smoke": True,
                        "validation_results.representative_searches": True,
                    },
                )
                transaction.update(
                    run_ref,
                    {
                        "status": "completed",
                        "completed_at": firestore_v1.SERVER_TIMESTAMP,
                        "validation_results.chunk_count": True,
                        "validation_results.vector_smoke": True,
                        "validation_results.representative_searches": True,
                    },
                )
                if previous_id and previous_id != manifest.generation_id:
                    previous_ref = self.client.collection("rag_generations").document(
                        str(previous_id)
                    )
                    transaction.update(previous_ref, {"status": "superseded"})

            activate(self.client.transaction())
        except (GoogleAPICallError, TypeError, ValueError) as exc:
            if staging_created:
                for reference in (generation_ref, run_ref):
                    try:
                        reference.update(
                            {
                                "status": "failed",
                                "completed_at": firestore_v1.SERVER_TIMESTAMP,
                                "validation_error": type(exc).__name__,
                            },
                            timeout=10,
                        )
                    except GoogleAPICallError:
                        pass
            raise PublicationError("Firestore generation publication failed") from exc

    async def publish_generation(
        self,
        manifest: GenerationManifest,
        chunks: Sequence[ChunkRecord],
    ) -> None:
        try:
            await asyncio.to_thread(self._publish_generation_sync, manifest, chunks)
        except GoogleAPICallError as exc:
            raise PublicationError("Firestore generation publication failed") from exc

    def _rollback_generation_sync(
        self,
        target_generation_id: str | None,
        rollback_id: str,
    ) -> RollbackReport:
        rollback_id = validate_generation_id(rollback_id)
        current = self.current_ref.get(timeout=10)
        current_data = current.to_dict() or {}
        current_id_value = current_data.get("generation_id")
        if not current.exists or not current_id_value:
            raise RollbackError("No active Firestore generation is available to roll back")
        current_id = str(current_id_value)
        target_value = target_generation_id or current_data.get("previous_generation_id")
        if not target_value:
            raise RollbackError("The active pointer does not name a previous generation")
        target_id = validate_generation_id(str(target_value))
        if target_id == current_id:
            raise RollbackError("The rollback target is already active")

        target_ref = self.client.collection("rag_generations").document(target_id)
        target = target_ref.get(timeout=10)
        target_data = target.to_dict() or {}
        validations = dict(target_data.get("validation_results") or {})
        expected_count = target_data.get("chunk_count")
        if not target.exists or target_data.get("status") != "superseded":
            raise RollbackError("Rollback target is not a retained superseded generation")
        if (
            not validations.get("chunk_count")
            or not validations.get("vector_smoke")
            or not validations.get("representative_searches")
        ):
            raise RollbackError("Rollback target does not have completed publication validation")
        if not isinstance(expected_count, int) or expected_count <= 0:
            raise RollbackError("Rollback target has an invalid manifest chunk count")
        chunks = target_ref.collection("chunks")
        observed_count = sum(1 for _ in chunks.select(["chunk_id"]).stream(timeout=60))
        if observed_count != expected_count:
            raise RollbackError(
                f"Rollback target has {observed_count} chunks; expected {expected_count}"
            )

        embedding_dimensions = target_data.get("embedding_dimensions")
        if not isinstance(embedding_dimensions, int) or embedding_dimensions <= 0:
            raise RollbackError("Rollback target has invalid embedding dimensions")
        first_vector: list[float] | None = None
        for snapshot in chunks.select(["embedding"]).stream(timeout=20):
            vector = (snapshot.to_dict() or {}).get("embedding")
            if vector is not None:
                first_vector = [float(value) for value in vector]
            break
        if first_vector is None or len(first_vector) != embedding_dimensions:
            raise RollbackError("Rollback target has no valid vector for its smoke test")
        smoke_query = chunks.find_nearest(
            vector_field="embedding",
            query_vector=Vector(first_vector),
            limit=1,
            distance_measure=DistanceMeasure.COSINE,
            distance_result_field="vector_distance",
            distance_threshold=2.0,
        )
        if not smoke_query.get(timeout=20):
            raise RollbackError("Rollback target failed its current vector lookup smoke test")

        audit_ref = self.client.collection("rollback_runs").document(rollback_id)
        completed_at = firestore_v1.SERVER_TIMESTAMP

        @firestore_v1.transactional
        def activate_rollback(transaction: firestore_v1.Transaction) -> None:
            locked_current = self.current_ref.get(transaction=transaction)
            locked_current_data = locked_current.to_dict() or {}
            if str(locked_current_data.get("generation_id") or "") != current_id:
                raise RollbackError("Active generation changed while rollback was being validated")
            locked_target = target_ref.get(transaction=transaction)
            if (
                not locked_target.exists
                or (locked_target.to_dict() or {}).get("status") != "superseded"
            ):
                raise RollbackError("Rollback target changed while rollback was being validated")
            transaction.create(
                audit_ref,
                {
                    "rollback_id": rollback_id,
                    "from_generation_id": current_id,
                    "to_generation_id": target_id,
                    "status": "completed",
                    "completed_at": completed_at,
                    "validated_chunk_count": observed_count,
                    "vector_smoke": True,
                },
            )
            transaction.set(
                self.current_ref,
                {
                    "generation_id": target_id,
                    "previous_generation_id": current_id,
                    "activated_at": completed_at,
                    "rollback_id": rollback_id,
                },
            )
            transaction.update(
                target_ref,
                {
                    "status": "active",
                    "reactivated_at": completed_at,
                    "last_rollback_id": rollback_id,
                },
            )
            transaction.update(
                self.client.collection("rag_generations").document(current_id),
                {"status": "superseded", "superseded_at": completed_at},
            )

        activate_rollback(self.client.transaction())
        return RollbackReport(
            rollback_id=rollback_id,
            previous_generation_id=current_id,
            active_generation_id=target_id,
            completed_at=datetime.now(UTC),
        )

    async def rollback_generation(
        self,
        target_generation_id: str | None,
        rollback_id: str,
    ) -> RollbackReport:
        try:
            return await asyncio.to_thread(
                self._rollback_generation_sync,
                target_generation_id,
                rollback_id,
            )
        except RollbackError:
            raise
        except GoogleAPICallError as exc:
            raise RollbackError("Firestore generation rollback failed") from exc

    async def close(self) -> None:
        await asyncio.to_thread(self.client.close)
