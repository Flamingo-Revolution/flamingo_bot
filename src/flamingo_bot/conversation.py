"""Bounded client-held conversation memory used to resolve follow-up questions.

The service stores no conversation. Each request carries its own recent turns,
so this module owns the trimming, sanitation, and rewriting rules that decide
how much of that unverified client input is allowed to influence retrieval and
the grounded prompt.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from flamingo_bot.models import ConversationTurn

CONVERSATION_CONTRACT_VERSION = "2.0.0"

_CITATION_MARKER = re.compile(r"\[S\d+\]")
_WHITESPACE = re.compile(r"\s+")


def strip_citation_markers(text: str) -> str:
    """Remove evidence markers from replayed text.

    Markers such as ``[S1]`` referred to evidence retrieved for an earlier
    question. Carrying them forward would let the model reuse a marker that now
    points at different evidence.
    """
    return _WHITESPACE.sub(" ", _CITATION_MARKER.sub(" ", text)).strip()


def normalize_history(
    turns: Sequence[ConversationTurn],
    *,
    max_turns: int,
    max_chars: int,
) -> list[ConversationTurn]:
    """Return the most recent usable turns within the configured budgets."""
    cleaned: list[ConversationTurn] = []
    for turn in turns:
        text = strip_citation_markers(turn.text) if turn.role == "assistant" else turn.text.strip()
        if text:
            cleaned.append(ConversationTurn(role=turn.role, text=text))

    if max_turns <= 0:
        return []

    budget = max_chars
    kept: list[ConversationTurn] = []
    for turn in reversed(cleaned[-max_turns:]):
        if budget <= 1:
            break
        # The ellipsis counts against the budget, so reserve a character for it.
        text = turn.text if len(turn.text) <= budget else turn.text[: budget - 1].rstrip() + "…"
        budget -= len(text)
        kept.append(ConversationTurn(role=turn.role, text=text))
    kept.reverse()
    return kept


def render_transcript(turns: Sequence[ConversationTurn]) -> str:
    speakers = {"user": "Visitor", "assistant": "Assistant"}
    return "\n".join(f"{speakers[turn.role]}: {turn.text}" for turn in turns)
