"""Minimal live Azure model smoke test that never prints content or credentials."""

from __future__ import annotations

import asyncio
import json

from flamingo_bot.config import Settings, load_model_config
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
        condensed = (
            await provider.complete_text(
                instructions="Reply only with the single word OK.",
                prompt="Reply only with OK.",
                safety_identifier=None,
            )
        ).strip()
        print(
            json.dumps(
                {
                    "embedding_count": len(vectors),
                    "embedding_dimensions": len(vectors[0]),
                    "chat_stream_nonempty": bool(answer),
                    "chat_characters": len(answer),
                    "condense_nonempty": bool(condensed),
                    "condense_characters": len(condensed),
                }
            )
        )
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
