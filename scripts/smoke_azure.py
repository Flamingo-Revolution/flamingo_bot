"""Minimal live Azure model smoke test that never prints content or credentials."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from flamingo_bot.config import Settings, load_model_config
from flamingo_bot.models import RetrievedChunk
from flamingo_bot.planning import (
    ANCHOR_INSTRUCTIONS,
    PLAN_INSTRUCTIONS,
    RANK_INSTRUCTIONS,
    build_plan_prompt,
    build_rank_prompt,
)
from flamingo_bot.providers.azure import AzureModelProvider


async def main() -> None:
    settings = Settings()
    provider = AzureModelProvider(settings, load_model_config(settings.model_config_path))
    try:
        vectors = await provider.embed_texts(["Flamingo Revolution"])
        answer_parts: list[str] = []
        async for delta in provider.stream_answer(
            instructions="Follow the request exactly.",
            prompt="Reply only with OK.",
            safety_identifier=None,
        ):
            answer_parts.append(delta)
        answer = "".join(answer_parts).strip()
        today = datetime.now(UTC).date()
        plan = await provider.plan_search(
            instructions=PLAN_INSTRUCTIONS + ANCHOR_INSTRUCTIONS,
            prompt=build_plan_prompt("What is the Flamingo Revolution?", [], today),
            safety_identifier=None,
        )
        selection = await provider.select_passages(
            instructions=RANK_INSTRUCTIONS,
            prompt=build_rank_prompt(
                "What is the Flamingo Revolution?",
                [],
                plan,
                [
                    RetrievedChunk(
                        chunk_id="smoke",
                        source_id="flamingo-revolution",
                        source_label="Synthetic smoke fixture",
                        title="Flamingo Revolution",
                        text="Synthetic smoke fixture: an independent civic information project.",
                        canonical_url="https://example.org/smoke",
                        distance=0,
                    ),
                ],
                today,
            ),
            safety_identifier=None,
        )
        print(
            json.dumps(
                {
                    "embedding_count": len(vectors),
                    "embedding_dimensions": len(vectors[0]),
                    "chat_stream_nonempty": bool(answer),
                    "chat_characters": len(answer),
                    "planner_queries": len(plan.queries),
                    "ranking_selected": len(selection.selected_ids),
                }
            )
        )
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
