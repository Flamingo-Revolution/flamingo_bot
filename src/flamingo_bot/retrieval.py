"""Retrieval, diversification, grounding, and answer streaming."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Sequence

from flamingo_bot.config import Settings
from flamingo_bot.conversation import (
    CONDENSE_INSTRUCTIONS,
    accept_condensed_query,
    build_condense_prompt,
    fallback_search_query,
    normalize_history,
)
from flamingo_bot.errors import FlamingoBotError
from flamingo_bot.models import Citation, ConversationTurn, RetrievedChunk
from flamingo_bot.ports import AnswerGenerator, Embedder, QueryCondenser, VectorStore
from flamingo_bot.prompting import SYSTEM_INSTRUCTIONS, build_grounded_prompt

logger = logging.getLogger("flamingo_bot.retrieval")

_ALBANIAN_MARKERS = {
    "çfarë",
    "është",
    "për",
    "dhe",
    "nga",
    "revolucioni",
    "shqipëri",
    "diaspora",
}

_SOURCE_IDS = (
    "flamingo-dossier",
    "flamingo-revolution",
    "diaspora-zbarkon",
)


def no_evidence_message(question: str) -> str:
    words = {word.strip(".,!?;:()[]\"'").lower() for word in question.split()}
    if any(character in question.lower() for character in "çë") or words & _ALBANIAN_MARKERS:
        return (
            "Nuk gjeta prova mjaftueshëm të rëndësishme në burimet e publikuara të "
            "Flamingos për t'iu përgjigjur me besueshmëri kësaj pyetjeje."
        )
    return (
        "I could not find enough relevant evidence in the published Flamingo "
        "sources to answer that question reliably."
    )


def diversify(chunks: list[RetrievedChunk], limit: int) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    source_counts: Counter[str] = Counter()
    seen_documents: set[tuple[str, str]] = set()
    for chunk in sorted(chunks, key=lambda item: item.distance):
        document_key = (chunk.source_id, str(chunk.metadata.get("record_id") or chunk.title))
        if document_key in seen_documents:
            continue
        if source_counts[chunk.source_id] >= 3:
            continue
        selected.append(chunk)
        seen_documents.add(document_key)
        source_counts[chunk.source_id] += 1
        if len(selected) == limit:
            break
    return selected


def citations_for(chunks: list[RetrievedChunk]) -> list[Citation]:
    citations: list[Citation] = []
    for index, chunk in enumerate(chunks, start=1):
        citations.append(
            Citation(
                id=f"S{index}",
                title=chunk.title,
                url=chunk.canonical_url,
                source=chunk.source_label,
            )
        )
    return citations


class ChatService:
    def __init__(
        self,
        *,
        settings: Settings,
        embedder: Embedder,
        vector_store: VectorStore,
        answer_generator: AnswerGenerator,
        query_condenser: QueryCondenser | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.vector_store = vector_store
        self.answer_generator = answer_generator
        self.query_condenser = query_condenser

    async def search_query(
        self,
        question: str,
        history: Sequence[ConversationTurn],
        *,
        safety_identifier: str | None = None,
    ) -> str:
        """Rewrite a follow-up into a standalone query for retrieval.

        A first question is already standalone, so it skips the extra model
        call. Condensation is best-effort: any failure or unusable answer falls
        back to a deterministic query instead of failing the chat request.
        """
        if not history:
            return question
        if self.query_condenser is None:
            return fallback_search_query(question, history)
        try:
            completion = await self.query_condenser.complete_text(
                instructions=CONDENSE_INSTRUCTIONS,
                prompt=build_condense_prompt(question, history),
                safety_identifier=safety_identifier,
            )
        except FlamingoBotError as exc:
            logger.warning("condense_failed error=%s", type(exc).__name__)
            return fallback_search_query(question, history)
        condensed = accept_condensed_query(completion)
        if condensed is None:
            logger.warning("condense_rejected reason=unusable_output")
            return fallback_search_query(question, history)
        return condensed

    async def retrieve(self, question: str) -> list[RetrievedChunk]:
        vectors = await self.embedder.embed_texts([question])
        per_source_limit = min(3, self.settings.flamingo_retrieval_candidates)
        source_results = await asyncio.gather(
            *(
                self.vector_store.search(
                    vectors[0],
                    limit=per_source_limit,
                    distance_threshold=self.settings.flamingo_relevance_distance_threshold,
                    source_id=source_id,
                )
                for source_id in _SOURCE_IDS
            )
        )
        candidates = [chunk for result in source_results for chunk in result]
        return diversify(candidates, self.settings.flamingo_retrieval_candidates)

    async def stream(
        self,
        question: str,
        *,
        history: Sequence[ConversationTurn] = (),
        safety_identifier: str | None = None,
    ) -> tuple[list[Citation], AsyncIterator[str]]:
        turns = normalize_history(
            history,
            max_turns=self.settings.flamingo_history_turns,
            max_chars=self.settings.flamingo_history_chars,
        )
        query = await self.search_query(question, turns, safety_identifier=safety_identifier)
        chunks = await self.retrieve(query)
        if not chunks:

            async def no_evidence() -> AsyncIterator[str]:
                yield no_evidence_message(query)

            return [], no_evidence()
        citations = citations_for(chunks)
        prompt = build_grounded_prompt(question, chunks, history=turns)
        stream = self.answer_generator.stream_answer(
            instructions=SYSTEM_INSTRUCTIONS,
            prompt=prompt,
            safety_identifier=safety_identifier,
        )
        return citations, stream
