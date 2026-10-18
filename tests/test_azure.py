from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from flamingo_bot.config import load_model_config
from flamingo_bot.errors import ProviderError
from flamingo_bot.planning import SearchPlan
from flamingo_bot.providers.azure import AzureModelProvider


class Client:
    def __init__(self, response: Any) -> None:
        self.responses = SimpleNamespace(create=AsyncMock(return_value=response))
        self.options: dict[str, Any] = {}

    def with_options(self, **kwargs: Any) -> "Client":
        self.options = kwargs
        return self


def provider() -> AzureModelProvider:
    result = object.__new__(AzureModelProvider)
    result.config = load_model_config(Path("openai.yaml"))
    return result


@pytest.mark.asyncio
async def test_planner_and_reranker_use_independent_strict_json_roles() -> None:
    models = provider()
    plan_client = Client(
        SimpleNamespace(
            status="completed", output_text=SearchPlan.fallback("Question").model_dump_json()
        )
    )
    rank_client = Client(SimpleNamespace(status="completed", output_text='{"selected_ids":["C1"]}'))
    models.condense_client = plan_client  # type: ignore[assignment]
    models.rerank_client = rank_client  # type: ignore[assignment]
    await models.plan_search(
        instructions="Rules", prompt="Question", safety_identifier="hashed-visitor"
    )
    await models.select_passages(
        instructions="Rules", prompt="Passages", safety_identifier="hashed-visitor"
    )
    for client, expected in ((plan_client, "gpt-6-luna"), (rank_client, "gpt-5.6-luna")):
        call = client.responses.create.call_args.kwargs
        assert call["model"] == expected
        assert call["reasoning"] == {"effort": "none"}
        assert call["store"] is False
        assert call["safety_identifier"] == "hashed-visitor"
        assert call["text"]["format"]["strict"] is True
        assert client.options == {"max_retries": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,text", [("incomplete", "private failure details"), ("completed", "not JSON")]
)
async def test_invalid_planner_output_is_not_accepted_or_exposed(status: str, text: str) -> None:
    models = provider()
    models.condense_client = Client(SimpleNamespace(status=status, output_text=text))  # type: ignore[assignment]
    with pytest.raises(ProviderError) as failure:
        await models.plan_search(instructions="Rules", prompt="Question", safety_identifier=None)
    assert text not in str(failure.value)


class Stream:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.closed = False

    async def __aenter__(self) -> "Stream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True

    async def __aiter__(self) -> Any:
        for event in self.events:
            yield event


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["response.completed", "response.incomplete", "missing"])
async def test_sol_stream_requires_completion_and_closes_on_failure(terminal: str) -> None:
    models = provider()
    events = [SimpleNamespace(type="response.output_text.delta", delta="Answer [S1].")]
    if terminal != "missing":
        events.append(SimpleNamespace(type=terminal))
    stream = Stream(events)
    client = Client(stream)
    models.chat_client = client  # type: ignore[assignment]

    async def consume() -> str:
        return "".join(
            [
                part
                async for part in models.stream_answer(
                    instructions="Rules", prompt="Evidence", safety_identifier=None
                )
            ]
        )

    if terminal == "response.completed":
        assert await consume() == "Answer [S1]."
    else:
        with pytest.raises(ProviderError):
            await consume()
    assert stream.closed
    assert client.responses.create.call_args.kwargs["model"] == "gpt-6.1-sol"
    assert client.responses.create.call_args.kwargs["reasoning"] == {"effort": "low"}
