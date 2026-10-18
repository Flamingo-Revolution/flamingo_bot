"""Retrieval, diversification, grounding, and answer streaming."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime
from urllib.parse import urlparse

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
    "lapsi",
)

# A narrow ambiguity rule: named places, dates, day numbers and other specific
# terms fall outside this vocabulary and retain the visitor's original scope.
_GENERIC_PROTEST_WORDS = set(
    """
did do does has have had was were is are the a an this these that it
protest protests demonstration demonstrations march marches movement flamingo revolution
turn turned become became get got violent violence peaceful police force clash clashes
escalate escalated escalation why how what happened there any been and or before after
still recent recently now latest
a u be bere bene eshte ishte jane kane ka pati patur ndodhur ndodhi
protesta protestat proteste protestes protesten marshimi marshimet levizja revolucioni
dhune dhuna dhunshem dhunshme policia polici policore perplasje paqe paqesore
pershkallezua pershkallezim pse si cfare ne te e per apo dhe tani fundit keto deri ende
""".split()
)


def contextualize_protest_query(question: str, *, today: date | None = None) -> str:
    """Resolve a generic movement-escalation question for multilingual retrieval."""
    normalized = unicodedata.normalize("NFKD", question.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    words = set(re.findall(r"\w+", normalized))
    protest = any(word.startswith(("protest", "demonstr", "marsh", "march")) for word in words)
    escalation = any(
        word.startswith(("violen", "dhun", "clash", "escalat", "perplas", "pershkallez"))
        for word in words
    )
    if not protest or not escalation or not words <= _GENERIC_PROTEST_WORDS:
        return question
    current = today or datetime.now(UTC).date()
    return (
        "Flamingo Revolution protests in Albania: recent escalation, police actions, "
        f"violence and clashes, {current:%B %Y}. {question}"
    )


def no_evidence_message(question: str) -> str:
    words = {word.strip(".,!?;:()[]\"'").lower() for word in question.split()}
    if any(character in question.lower() for character in "çë") or words & _ALBANIAN_MARKERS:
        return (
            "Nuk gjeta prova mjaftueshëm të rëndësishme në burimet e publikuara të "
            "Flamingos për t'iu përgjigjur me besueshmëri kësaj pyetjeje.\n\n"
            "Mund të të ndihmoj me Revolucionin Flamingo dhe protestat, me skandalet "
            "e dokumentuara në Dosjen Flamingo, dhe me pjesëmarrjen qytetare e "
            "diasporën te Diaspora Zbarkon. Provo një pyetje më të përcaktuar, për "
            "shembull për një çështje, një protestë ose një person të caktuar."
        )
    return (
        "I could not find enough relevant evidence in the published Flamingo "
        "sources to answer that question reliably.\n\n"
        "I can help with the Flamingo Revolution and its protests, the scandals "
        "documented in the Flamingo Dossier, and civic participation and diaspora "
        "mobilisation from Diaspora Zbarkon. Try a more specific question, for "
        "example about a particular case, protest, or person."
    )


def _recency_bonus(chunk: RetrievedChunk, as_of: date) -> float:
    for field in ("event_date", "reported_event_date", "research_date"):
        value = chunk.metadata.get(field)
        if not isinstance(value, str) or not value:
            continue
        try:
            evidence_date = date.fromisoformat(value)
        except ValueError:
            continue
        age = (as_of - evidence_date).days
        # Filing dates affect ranking only, never the date stated for an incident.
        return 0.08 * max(0.0, 1.0 - age / 30) if age >= 0 else 0.0
    return 0.0


def diversify(
    chunks: list[RetrievedChunk], limit: int, *, prefer_recent_as_of: date | None = None
) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    source_counts: Counter[str] = Counter()
    seen_documents: set[tuple[str, str]] = set()
    seen_articles: set[str] = set()

    def lapsi_article_keys(item: RetrievedChunk) -> set[str]:
        aliases = item.metadata.get("aliases")
        urls = [item.canonical_url, *(aliases if isinstance(aliases, list) else [])]
        keys: set[str] = set()
        for url in urls:
            if not isinstance(url, str):
                continue
            parsed = urlparse(url)
            if parsed.hostname not in {"lapsi.al", "www.lapsi.al"}:
                continue
            path = parsed.path.rstrip("/")
            match = re.search(r"-i(\d+)$", path)
            keys.add("lapsi:" + match.group(1) if match else "lapsi:" + path)
        return keys

    detailed_article_keys = {
        key for chunk in chunks if chunk.metadata.get("content_use") != "reference"
        for key in lapsi_article_keys(chunk)
    }

    def rank(item: RetrievedChunk) -> float:
        bonus = _recency_bonus(item, prefer_recent_as_of) if prefer_recent_as_of else 0.0
        return item.distance - bonus

    for chunk in sorted(chunks, key=rank):
        article_keys = lapsi_article_keys(chunk)
        if article_keys & seen_articles:
            continue
        if (
            chunk.metadata.get("content_use") == "reference"
            and article_keys & detailed_article_keys
        ):
            continue
        document_key = (chunk.source_id, str(chunk.metadata.get("record_id") or chunk.title))
        if document_key in seen_documents:
            continue
        if source_counts[chunk.source_id] >= 3:
            continue
        selected.append(chunk)
        seen_documents.add(document_key)
        seen_articles.update(article_keys)
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
        as_of = datetime.now(UTC).date()
        query = contextualize_protest_query(question, today=as_of)
        prefer_recent = query != question
        vectors = await self.embedder.embed_texts([query])
        # Gather enough candidates to survive document/language deduplication.
        # Only the final 5-7 diversified passages reach the answer model.
        per_source_limit = self.settings.flamingo_retrieval_candidates * (2 if prefer_recent else 1)
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
        if prefer_recent and candidates:
            # Do not fill a current-events answer with weakly related historical
            # dossiers just because the final context has spare slots.
            best_distance = min(chunk.distance for chunk in candidates)
            candidates = [chunk for chunk in candidates if chunk.distance <= best_distance + 0.18]
        return diversify(
            candidates,
            self.settings.flamingo_retrieval_candidates,
            prefer_recent_as_of=as_of if prefer_recent else None,
        )

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
                yield no_evidence_message(question)

            return [], no_evidence()
        citations = citations_for(chunks)
        prompt = build_grounded_prompt(question, chunks, history=turns)
        stream = self.answer_generator.stream_answer(
            instructions=SYSTEM_INSTRUCTIONS,
            prompt=prompt,
            safety_identifier=safety_identifier,
        )
        return citations, stream
