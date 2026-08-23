from flamingo_bot.conversation import (
    accept_condensed_query,
    build_condense_prompt,
    fallback_search_query,
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


def test_condense_prompt_delimits_the_untrusted_transcript() -> None:
    prompt = build_condense_prompt("Ma thuaj shkurt.", [turn("user", "Pyetja e parë")])

    assert "<conversation>" in prompt
    assert "</conversation>" in prompt
    assert prompt.rstrip().endswith("Standalone search query:")
    assert "Latest visitor message:\nMa thuaj shkurt." in prompt


def test_fallback_query_carries_the_previous_visitor_question() -> None:
    turns = [
        turn("user", "Çfarë është Revolucioni Flamingo?"),
        turn("assistant", "Një lëvizje qytetare."),
    ]

    assert (
        fallback_search_query("Ma thuaj shkurt.", turns)
        == "Çfarë është Revolucioni Flamingo? Ma thuaj shkurt."
    )


def test_fallback_query_is_the_question_when_no_visitor_turn_exists() -> None:
    assert fallback_search_query("Ma thuaj shkurt.", [turn("assistant", "Përgjigjja")]) == (
        "Ma thuaj shkurt."
    )


def test_condensed_query_is_accepted_from_the_first_usable_line() -> None:
    assert accept_condensed_query('\n  "Aeroporti i Vlorës"  \nextra line') == "Aeroporti i Vlorës"


def test_unusable_condensation_output_is_rejected() -> None:
    assert accept_condensed_query("") is None
    assert accept_condensed_query("   \n  ") is None
    assert accept_condensed_query("x" * 401) is None
