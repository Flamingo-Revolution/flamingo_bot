"""Python ingestion entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from flamingo_bot.chunking import CHUNKER_VERSION, SemanticChunker
from flamingo_bot.config import ModelConfig, Settings, load_model_config
from flamingo_bot.errors import FlamingoBotError, PublicationError, SourceValidationError
from flamingo_bot.models import (
    EmbeddingReuseContract,
    GenerationManifest,
    IngestionCounts,
    IngestionReport,
)
from flamingo_bot.parsers import NORMALIZATION_VERSION, PARSER_VERSION, parse_sources
from flamingo_bot.providers.azure import AzureModelProvider
from flamingo_bot.providers.firestore import FirestoreVectorStore
from flamingo_bot.sources import load_source_catalog, resolve_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Flamingo Bot knowledge sources")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Parse and report without writes")
    mode.add_argument(
        "--publish", action="store_true", help="Stage and activate a Firestore generation"
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip active-generation comparison; valid only with --dry-run",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON report")
    return parser


def reuse_contract_for(model_config: ModelConfig) -> EmbeddingReuseContract:
    embedding = model_config.embedding
    return EmbeddingReuseContract(
        embedding_deployment=embedding.deployment,
        embedding_dimensions=embedding.dimensions,
        parser_version=PARSER_VERSION,
        chunker_version=CHUNKER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
    )


def removed_chunk_count(active_counts: dict[str, int], current_counts: Counter[str]) -> int:
    return sum(
        max(active_count - current_counts.get(content_hash, 0), 0)
        for content_hash, active_count in active_counts.items()
    )


async def run_dry_run(settings: Settings, *, local_only: bool = False) -> IngestionReport:
    started_at = datetime.now(UTC)
    model_config = load_model_config(settings.model_config_path)
    catalog = load_source_catalog(settings.source_catalog_path)
    sources = resolve_sources(settings, catalog)
    documents, issues, discovered_files = parse_sources(sources)
    try:
        chunks = SemanticChunker().chunk_documents(documents)
    except ValueError as exc:
        raise SourceValidationError("Unable to chunk the configured sources") from exc
    current_counts = Counter(chunk.content_hash for chunk in chunks)
    if local_only:
        active_generation_id = None
        change_set_basis: Literal[
            "active-generation", "no-active-generation", "local-source-only"
        ] = "local-source-only"
        reused_embeddings = 0
        new_chunks = len(chunks)
        removed_chunks = 0
    else:
        vector_store = FirestoreVectorStore(settings)
        try:
            snapshot = await vector_store.load_active_snapshot(reuse_contract_for(model_config))
        finally:
            await vector_store.close()
        active_generation_id = snapshot.generation_id
        change_set_basis = "active-generation" if active_generation_id else "no-active-generation"
        reused_embeddings = sum(
            1
            for chunk in chunks
            if len(snapshot.embeddings.get(chunk.content_hash, []))
            == model_config.embedding.dimensions
        )
        new_chunks = len(chunks) - reused_embeddings
        removed_chunks = removed_chunk_count(snapshot.content_hash_counts, current_counts)
    completed_at = datetime.now(UTC)
    failed = sum(1 for issue in issues if not issue.reason.startswith("No meaningful"))
    counts = IngestionCounts(
        discovered_files=discovered_files,
        accepted_documents=len(documents),
        skipped_files=len(issues) - failed,
        chunks=len(chunks),
        new_chunks=new_chunks,
        reused_embeddings=reused_embeddings,
        removed_chunks=removed_chunks,
        failed_files=failed,
    )
    return IngestionReport(
        mode="dry-run",
        started_at=started_at,
        completed_at=completed_at,
        counts=counts,
        source_revisions={source.definition.id: source.revision for source in sources},
        issues=issues,
        published=False,
        generation_id=None,
        active_generation_id=active_generation_id,
        change_set_basis=change_set_basis,
    )


async def run_publish(settings: Settings) -> IngestionReport:
    started_at = datetime.now(UTC)
    model_config = load_model_config(settings.model_config_path)
    catalog = load_source_catalog(settings.source_catalog_path)
    sources = resolve_sources(settings, catalog)
    documents, issues, discovered_files = parse_sources(sources)
    try:
        chunks = SemanticChunker().chunk_documents(documents)
    except ValueError as exc:
        raise SourceValidationError("Unable to chunk the configured sources") from exc
    failed = sum(1 for issue in issues if not issue.reason.startswith("No meaningful"))
    if failed:
        raise PublicationError(f"Refusing publication because {failed} source files failed")
    if not chunks:
        raise PublicationError("Refusing to publish an empty generation")
    source_document_counts = Counter(document.source_id for document in documents)
    missing_sources = [
        source.definition.id
        for source in sources
        if source_document_counts[source.definition.id] == 0
    ]
    if missing_sources:
        raise PublicationError(
            "Refusing publication because sources have no documents: " + ", ".join(missing_sources)
        )

    vector_store = FirestoreVectorStore(settings)
    model_provider = AzureModelProvider(settings, model_config)
    try:
        snapshot = await vector_store.load_active_snapshot(reuse_contract_for(model_config))
        active_embeddings = snapshot.embeddings
        pending = []
        reused = 0
        for chunk in chunks:
            existing = active_embeddings.get(chunk.content_hash)
            if existing is not None and len(existing) == model_config.embedding.dimensions:
                chunk.embedding = existing
                reused += 1
            else:
                pending.append(chunk)
        if pending:
            vectors = await model_provider.embed_texts([chunk.text for chunk in pending])
            for chunk, vector in zip(pending, vectors, strict=True):
                chunk.embedding = vector

        source_revisions = {source.definition.id: source.revision for source in sources}
        current_counts = Counter(chunk.content_hash for chunk in chunks)
        removed_chunks = removed_chunk_count(snapshot.content_hash_counts, current_counts)
        generation_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "source_revisions": source_revisions,
                    "chunk_hashes": [chunk.content_hash for chunk in chunks],
                    "parser_version": PARSER_VERSION,
                    "chunker_version": CHUNKER_VERSION,
                    "normalization_version": NORMALIZATION_VERSION,
                    "embedding": model_config.embedding.model_dump(),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:12]
        generation_id = f"{started_at:%Y%m%dT%H%M%SZ}-{generation_fingerprint}"
        manifest = GenerationManifest(
            generation_id=generation_id,
            status="staging",
            created_at=started_at,
            source_revisions=source_revisions,
            parser_version=PARSER_VERSION,
            chunker_version=CHUNKER_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            embedding_deployment=model_config.embedding.deployment,
            embedding_dimensions=model_config.embedding.dimensions,
            chunk_count=len(chunks),
            document_count=len(documents),
            discovered_file_count=discovered_files,
            skipped_file_count=len(issues),
            failed_file_count=0,
            reused_embedding_count=reused,
            embedded_chunk_count=len(pending),
            removed_chunk_count=removed_chunks,
            source_document_counts=dict(source_document_counts),
            validation_results={
                "source_coverage": True,
                "embedding_dimensions": True,
            },
        )
        await vector_store.publish_generation(manifest, chunks)
    finally:
        try:
            await model_provider.close()
        finally:
            await vector_store.close()

    completed_at = datetime.now(UTC)
    return IngestionReport(
        mode="publish",
        started_at=started_at,
        completed_at=completed_at,
        counts=IngestionCounts(
            discovered_files=discovered_files,
            accepted_documents=len(documents),
            skipped_files=len(issues),
            chunks=len(chunks),
            new_chunks=len(pending),
            reused_embeddings=reused,
            embedded_chunks=len(pending),
            removed_chunks=removed_chunks,
            failed_files=0,
        ),
        source_revisions=source_revisions,
        issues=issues,
        generation_id=generation_id,
        active_generation_id=generation_id,
        change_set_basis=(
            "active-generation" if snapshot.generation_id else "no-active-generation"
        ),
        published=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.local_only and not args.dry_run:
        parser.error("--local-only is valid only with --dry-run")
    settings = Settings()
    try:
        report = (
            asyncio.run(run_publish(settings))
            if args.publish
            else asyncio.run(run_dry_run(settings, local_only=args.local_only))
        )
    except FlamingoBotError as exc:
        print(
            json.dumps({"error": type(exc).__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return 2
    print(report.model_dump_json(indent=2 if args.pretty else None))
    return 0 if report.counts.failed_files == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
