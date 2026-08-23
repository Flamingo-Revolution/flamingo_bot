"""Explicit CLI for a validated Firestore knowledge-generation rollback."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from flamingo_bot.config import Settings
from flamingo_bot.errors import FlamingoBotError
from flamingo_bot.providers.firestore import FirestoreVectorStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Roll back the active RAG generation")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--to", metavar="GENERATION_ID", help="Activate a retained generation")
    target.add_argument(
        "--previous",
        action="store_true",
        help="Activate rag_meta/current.previous_generation_id",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON report")
    return parser


async def run(target_generation_id: str | None, settings: Settings) -> dict[str, object]:
    now = datetime.now(UTC)
    rollback_id = f"rollback-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    store = FirestoreVectorStore(settings)
    try:
        report = await store.rollback_generation(target_generation_id, rollback_id)
        return report.model_dump(mode="json")
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(run(None if args.previous else args.to, Settings()))
    except FlamingoBotError as exc:
        print(
            json.dumps({"error": type(exc).__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
