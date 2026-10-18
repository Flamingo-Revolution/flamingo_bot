"""Azure-hosted OpenAI model access through the official Python SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from openai import APIError, AsyncAzureOpenAI
from pydantic import BaseModel, ValidationError

from flamingo_bot.config import ChatModelConfig, ModelConfig, Settings
from flamingo_bot.errors import ProviderError
from flamingo_bot.planning import PassageSelection, SearchPlan


class AzureModelProvider:
    def __init__(self, settings: Settings, config: ModelConfig) -> None:
        endpoint, api_key = settings.require_azure_credentials()
        self.config = config
        self.chat_client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=config.chat.api_version,
            timeout=config.chat.timeout_seconds,
            max_retries=0,
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
        self.rerank_client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=config.rerank.api_version,
            timeout=config.rerank.timeout_seconds,
            max_retries=0,
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

    async def _structured_completion(
        self,
        *,
        client: AsyncAzureOpenAI,
        config: ChatModelConfig,
        schema: type[BaseModel],
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> str:
        """Use strict JSON without retaining visitor input or logging provider payloads."""
        try:
            response = await client.with_options(max_retries=0).responses.create(
                model=config.deployment,
                instructions=instructions,
                input=prompt,
                reasoning={"effort": config.reasoning_effort},
                text={
                    "verbosity": config.text_verbosity,
                    "format": {
                        "type": "json_schema",
                        "name": schema.__name__,
                        "schema": schema.model_json_schema(),
                        "strict": True,
                    },
                },
                max_output_tokens=config.max_output_tokens,
                safety_identifier=safety_identifier,
                store=False,
            )
        except APIError as exc:
            raise ProviderError("Azure structured retrieval call failed") from exc
        if response.status != "completed":
            raise ProviderError("Azure structured retrieval response was incomplete")
        return response.output_text or ""

    async def plan_search(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> SearchPlan:
        text = await self._structured_completion(
            client=self.condense_client,
            config=self.config.condense,
            schema=SearchPlan,
            instructions=instructions,
            prompt=prompt,
            safety_identifier=safety_identifier,
        )
        try:
            return SearchPlan.model_validate_json(text)
        except ValidationError as exc:
            raise ProviderError("Azure returned an invalid retrieval plan") from exc

    async def select_passages(
        self,
        *,
        instructions: str,
        prompt: str,
        safety_identifier: str | None,
    ) -> PassageSelection:
        text = await self._structured_completion(
            client=self.rerank_client,
            config=self.config.rerank,
            schema=PassageSelection,
            instructions=instructions,
            prompt=prompt,
            safety_identifier=safety_identifier,
        )
        try:
            return PassageSelection.model_validate_json(text)
        except ValidationError as exc:
            raise ProviderError("Azure returned an invalid passage selection") from exc

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
            completed = False
            received_text = False
            async with stream:
                async for event in stream:
                    event_type = getattr(event, "type", "")
                    if event_type == "response.output_text.delta":
                        delta = getattr(event, "delta", "")
                        if delta:
                            received_text = True
                            yield str(delta)
                    elif event_type in {"response.failed", "response.incomplete"}:
                        raise ProviderError(f"Azure response stream ended as {event_type}")
                    elif event_type == "response.completed":
                        completed = True
            if not completed or not received_text:
                raise ProviderError("Azure response stream was empty or had no completion event")
        except APIError as exc:
            raise ProviderError("Azure answer request failed") from exc

    async def close(self) -> None:
        await self.chat_client.close()
        await self.condense_client.close()
        await self.embedding_client.close()
        await self.rerank_client.close()
