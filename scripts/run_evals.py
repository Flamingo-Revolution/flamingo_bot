"""Run automatic citation-contract checks against a live local or deployed API."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yaml

#: Markdown the widget does not render, which a visitor would read as literal
#: characters: headings, fenced code, tables, blockquotes, and links.
UNSUPPORTED_MARKDOWN = re.compile(
    r"(?m)^\s{0,3}#{1,6}\s|```|^\s{0,3}\|.*\|\s*$|^\s{0,3}>\s|\[[^\]]+\]\(",
)


@dataclass(frozen=True)
class ConversationTurn:
    role: str
    text: str


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    history: tuple[ConversationTurn, ...] = ()
    min_citations: int = 0
    max_citations: int | None = None
    expected_source_any: tuple[str, ...] = ()
    expected_source_all: tuple[str, ...] = ()
    forbidden_answer_phrases: tuple[str, ...] = ()
    #: Cases whose correct outcome is a refusal must not be forced to cite,
    #: because retrieval can still return loosely related chunks.
    expect_citation_markers: bool = True


@dataclass
class EvalResult:
    id: str
    history_turns: int = 0
    passed: bool = True
    citation_count: int = 0
    answer_characters: int = 0
    failures: list[str] = field(default_factory=list)


def load_history(raw: Any) -> tuple[ConversationTurn, ...]:
    if not isinstance(raw, list):
        raise ValueError("Evaluation history must be a list of turns")
    turns: list[ConversationTurn] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each history turn must be an object")
        role = str(item["role"])
        if role not in {"user", "assistant"}:
            raise ValueError("History roles must be user or assistant")
        turns.append(ConversationTurn(role=role, text=str(item["text"])))
    return tuple(turns)


def load_cases(path: Path) -> list[EvalCase]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ValueError("Evaluation file must contain a cases list")
    cases: list[EvalCase] = []
    for item in raw["cases"]:
        if not isinstance(item, dict):
            raise ValueError("Each evaluation case must be an object")
        cases.append(
            EvalCase(
                id=str(item["id"]),
                question=str(item["question"]),
                history=load_history(item.get("history", [])),
                min_citations=int(item.get("min_citations", 0)),
                max_citations=(
                    int(item["max_citations"]) if item.get("max_citations") is not None else None
                ),
                expected_source_any=tuple(
                    str(value) for value in item.get("expected_source_any", [])
                ),
                expected_source_all=tuple(
                    str(value) for value in item.get("expected_source_all", [])
                ),
                forbidden_answer_phrases=tuple(
                    str(value) for value in item.get("forbidden_answer_phrases", [])
                ),
                expect_citation_markers=bool(item.get("expect_citation_markers", True)),
            )
        )
    return cases


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if not data_lines:
            continue
        data = json.loads("\n".join(data_lines))
        if not isinstance(data, dict):
            raise ValueError("SSE data must be a JSON object")
        events.append((event_name, data))
    return events


def run_case(case: EvalCase, api_base: str, origin: str, timeout: float) -> EvalResult:
    result = EvalResult(id=case.id, history_turns=len(case.history))
    request = Request(
        f"{api_base.rstrip('/')}/v1/chat",
        data=json.dumps(
            {
                "question": case.question,
                "history": [{"role": turn.role, "text": turn.text} for turn in case.history],
            }
        ).encode(),
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Origin": origin,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            events = parse_sse(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        result.passed = False
        result.failures.append(type(exc).__name__)
        return result

    citations = [data for event, data in events if event == "citation"]
    answer = "".join(
        str(data.get("text", "")) for event, data in events if event == "delta"
    ).strip()
    sources = {str(citation.get("source", "")) for citation in citations}
    result.citation_count = len(citations)
    result.answer_characters = len(answer)

    if not answer:
        result.failures.append("empty_answer")
    if not any(event == "done" for event, _ in events):
        result.failures.append("missing_done_event")
    if any(event == "error" for event, _ in events):
        result.failures.append("stream_error")
    if len(citations) < case.min_citations:
        result.failures.append("too_few_citations")
    if case.max_citations is not None and len(citations) > case.max_citations:
        result.failures.append("too_many_citations")
    if case.expected_source_any and not sources.intersection(case.expected_source_any):
        result.failures.append("expected_source_missing")
    if case.expected_source_all and not set(case.expected_source_all).issubset(sources):
        result.failures.append("required_sources_missing")
    if (
        case.expect_citation_markers
        and citations
        and not any(str(citation.get("id", "")) in answer for citation in citations)
    ):
        result.failures.append("citation_markers_missing_from_answer")
    lowered_answer = answer.lower()
    if any(phrase.lower() in lowered_answer for phrase in case.forbidden_answer_phrases):
        result.failures.append("forbidden_phrase_present")
    if UNSUPPORTED_MARKDOWN.search(answer):
        result.failures.append("unsupported_markdown_in_answer")

    result.passed = not result.failures
    return result


def valid_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("value must be an HTTP(S) URL")
    return value.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", type=valid_http_url, default="http://localhost:8000")
    parser.add_argument("--origin", type=valid_http_url, default="http://localhost:5173")
    parser.add_argument("--cases", type=Path, default=Path("evals/questions.yaml"))
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    results = [
        run_case(case, args.api_base, args.origin, args.timeout) for case in load_cases(args.cases)
    ]
    payload = {
        "passed": all(result.passed for result in results),
        "manual_review_required": True,
        "results": [
            {
                "id": result.id,
                "history_turns": result.history_turns,
                "passed": result.passed,
                "citation_count": result.citation_count,
                "answer_characters": result.answer_characters,
                "failures": result.failures,
            }
            for result in results
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
