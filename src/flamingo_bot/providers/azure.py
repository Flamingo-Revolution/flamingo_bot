"""Azure-hosted OpenAI model access through the official Python SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from openai import APIError, AsyncAzureOpenAI

from flamingo_bot.config import ModelConfig, Settings
from flamingo_bot.errors import ProviderError


class AzureModelProvider:
    def __init__(self, settings: Settings, config: ModelConfig) -> None:
        endpoint, api_key = settings.require_azure_credentials()
        self.config = config
        self.chat_client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=config.chat.api_version,
            timeout=config.chat.timeout_seconds,
            max_retries=2,
        )
        self.condense_client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=config.condense.api_version,
            timeout=config.condense.timeout_seconds,
            max_retries=2,
        )
        self.embedding_client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=config.embedding.api_version,
            timeout=config.embedding.timeout_seconds,
            max_retries=2,
        )

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        batch_size = self.config.embedding.batch_size
        try:
            for index in range(0, len(texts), batch_size):
                batch = list(texts[index : index + batch_size])
                response = await self.embedding_client.embeddings.create(
                    model=self.config.embedding.deployment,
                    input=batch,
                    dimensions=self.config.embedding.dimensions,
                    encoding_format="float",
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                vectors = [list(item.embedding) for item in ordered]
                if len(vectors) != len(batch):
                    raise ProviderError("Azure returned an unexpected embedding count")
                if any(len(vector) != self.config.embedding.dimensions for vector in vectors):
                    raise ProviderError("Azure returned an unexpected embedding dimension")
                embeddings.extend(vectors)
        except APIError as exc:
            raise ProviderError("Azure embedding request failed") from exc
        return embeddings

    async def complete_text(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> str:
        """Run one short non-streaming completion used for query condensation."""
        try:
            response = await self.condense_client.responses.create(
                model=self.config.condense.deployment,
                instructions=instructions,
                input=prompt,
                reasoning={"effort": self.config.condense.reasoning_effort},
                text={"verbosity": self.config.condense.text_verbosity},
                max_output_tokens=self.config.condense.max_output_tokens,
                safety_identifier=safety_identifier,
                store=False,
            )
        except APIError as exc:
            raise ProviderError("Azure query condensation failed") from exc
        if response.status not in {"completed", None}:
            raise ProviderError(f"Azure condensation response ended as {response.status}")
        return response.output_text or ""

    async def stream_answer(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> AsyncIterator[str]:
        try:
            stream = await self.chat_client.responses.create(
                model=self.config.chat.deployment,
                instructions=instructions,
                input=prompt,
                reasoning={"effort": self.config.chat.reasoning_effort},
                text={"verbosity": self.config.chat.text_verbosity},
                max_output_tokens=self.config.chat.max_output_tokens,
                safety_identifier=safety_identifier,
                store=False,
                stream=True,
            )
            async for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        yield str(delta)
                elif event_type in {"response.failed", "response.incomplete"}:
                    raise ProviderError(f"Azure response stream ended as {event_type}")
        except APIError as exc:
            raise ProviderError("Azure answer request failed") from exc

    async def close(self) -> None:
        await self.chat_client.close()
        await self.condense_client.close()
        await self.embedding_client.close()
