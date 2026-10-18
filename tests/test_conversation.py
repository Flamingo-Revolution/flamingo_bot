from flamingo_bot.conversation import (
    normalize_history,
    render_transcript,
    strip_citation_markers,
)
from flamingo_bot.models import ConversationTurn


def turn(role: str, text: str) -> ConversationTurn:
    return ConversationTurn(role=role, text=text)  # type: ignore[arg-type]


def test_stale_citation_markers_are_removed_from_replayed_answers() -> None:
    cleaned = strip_citation_markers(
        "Flamingo Revolution is independent [S1]. Nature too [S2][S3]."
    )

    assert cleaned == "Flamingo Revolution is independent . Nature too ."
    assert "[S" not in cleaned


def test_normalize_keeps_only_the_most_recent_turns() -> None:
    turns = [turn("user", f"question {index}") for index in range(10)]

    kept = normalize_history(turns, max_turns=4, max_chars=4000)

    assert [item.text for item in kept] == [
        "question 6",
        "question 7",
        "question 8",
        "question 9",
    ]


def test_normalize_drops_blank_turns_and_strips_markers() -> None:
    turns = [
        turn("user", "  Çfarë është Revolucioni Flamingo?  "),
        turn("assistant", "Një lëvizje qytetare [S1]."),
        turn("assistant", "   "),
    ]

    kept = normalize_history(turns, max_turns=6, max_chars=4000)

    assert [(item.role, item.text) for item in kept] == [
        ("user", "Çfarë është Revolucioni Flamingo?"),
        ("assistant", "Një lëvizje qytetare ."),
    ]


def test_normalize_truncates_the_oldest_turn_to_fit_the_character_budget() -> None:
    turns = [turn("user", "a" * 300), turn("assistant", "b" * 120)]

    kept = normalize_history(turns, max_turns=6, max_chars=200)

    assert kept[-1].text == "b" * 120
    assert len(kept[0].text) == 80
    assert kept[0].text.endswith("…")
    assert sum(len(item.text) for item in kept) <= 200


def test_normalize_returns_nothing_when_memory_is_disabled() -> None:
    turns = [turn("user", "Çfarë është Revolucioni Flamingo?")]

    assert normalize_history(turns, max_turns=0, max_chars=4000) == []


def test_transcript_labels_the_visitor_and_the_assistant() -> None:
    transcript = render_transcript([turn("user", "Pyetja"), turn("assistant", "Përgjigjja")])

    assert transcript == "Visitor: Pyetja\nAssistant: Përgjigjja"
