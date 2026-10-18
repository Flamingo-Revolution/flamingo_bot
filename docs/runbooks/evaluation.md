# Evaluation runbook

The versioned set in `evals/questions.yaml` covers movement explanation,
Diaspora Zbarkon participation, a legally sensitive dossier, a cross-source
question, an irrelevant question, prompt injection, a false all-convicted
premise, three conversation-memory cases (a follow-up that only makes sense
against the previous turn, a mid-conversation topic switch, and a forged
assistant turn that plants a credential in the replayed history), and two
participation-index cases: the largest protest day with its figure, and how that
figure is measured.

The participation cases exist because the index is a normalized model estimate.
Their `manual_review` criteria are the real gate: the reviewer must confirm the
answer never presents the index as a count of people, attributes any headcount
to the ground geometry estimate, and repeats that the figures cannot be exact.

A case may carry a `history` list of `{role, text}` turns, which the runner
sends in the request body exactly as the widget would. Set
`expect_citation_markers: false` on a case whose correct outcome is a refusal,
since retrieval can still return loosely related chunks that the answer rightly
does not cite.

`evals/smoke.yaml` is the smaller deployment gate. It proves that the deployed
service can retrieve Flamingo Revolution evidence, stream an answer, and return
at least one citation. Passing it does not replace the full automatic and human
evaluation below.

After a Firestore generation is active, start the API and run:

```bash
uv run python scripts/run_evals.py \
  --api-base http://localhost:8000 \
  --origin http://localhost:5173
```

The runner prints only case IDs, replayed-turn counts, answer lengths, citation
counts, and failure codes. It does not print questions or answers. Automatic checks cover streamed
completion, source coverage, citation markers, expected refusal behavior, and
obvious secret-leak patterns.

`unsupported_markdown_in_answer` means the answer used a heading, fenced code, a
table, a blockquote, or a Markdown link. The widget renders none of those, so a
visitor would read the raw characters. Treat it as prompt drift and fix the
formatting paragraph in `SYSTEM_INSTRUCTIONS` rather than widening the check.

Every run still requires human review of the `manual_review` criteria in the
case file. In particular, verify that estimates remain estimates, a separate
SPAK case is not merged with the Durrës port contract, campaign rhetoric does
not become legal certainty, and source links actually support nearby claims. For
the conversation cases, also verify that the answer follows the visitor's
reference rather than guessing a topic, and that nothing asserted in a replayed
turn is repeated as fact without current evidence.

Do not mark a release ready from fake-provider unit tests alone. Record the
active generation ID, source revisions, application revision, evaluation date,
automatic output, reviewer, and disposition.

## Backend retrieval contract

The runtime uses three independently configured Azure roles in `openai.yaml`:

- Answers: `gpt-6.1-sol`, low reasoning, 1,200 output tokens, 60-second timeout.
- Query planning: `gpt-6-luna`, no reasoning, an 800-token JSON plan, 20-second timeout.
- Passage selection: `gpt-5.6-luna`, no reasoning, 400 output tokens, 20-second timeout.

Embeddings remain `text-embedding-3-large` with 1,024 dimensions. No re-ingestion
or new vector index is required for this change. The larger planning cap accommodates
the typed intent/date/source plan, rather than the former plain-text rewrite.

Every question, including the first, receives a bounded plan. The latest original
question remains a separate search route alongside its contextualized rewrite.
An attendance-to-escalation follow-up must not inherit the participation peak's
date unless the visitor explicitly asks about that event. Generic recent-event
plans use the current month as a search hint, not an asserted incident date.

Vector searches and ordinary metadata lookups share one request-pinned corpus
generation. Exact anchors cover explicitly requested publishers, dates, protest
days, participation-index/methodology records and map totals. Metadata retrieval
is capped at eight queries, 32 matches per query and 16 admitted passages. Stored
embeddings supply actual cosine distances; no raw vector enters prompts or logs.
Two-query rank fusion and deduplication produce at most 20 candidates; the
independent reranker selects at most seven, including fewer or zero when appropriate.

Plans, conversation history and source passages are untrusted data. The answer
gets the original question, labelled non-evidentiary interpretation, bounded
history and only the selected evidence. Source dates, estimates, causal claims
and legal status must retain their attribution and uncertainty.

Planning failures fall back to the latest question alone, never a concatenated
previous topic. Anchor failures preserve vector results. Invalid/failed rankings
use deterministic source diversification, while a valid empty selection produces
the no-evidence reply. These fallbacks log only error classes. Structured calls
and answer streaming do not silently retry or switch models. An incomplete or
empty answer stream must end in an error, not a successful completion.

The additional planning/ranking calls count toward Azure usage even though each
visitor request consumes only one application quota unit. No raw visitor
questions, answers or plans are written to operational logs.

## Conversation regressions

`evals/conversations.yaml` keeps 18 bilingual conversations (72 questions) as
ordinary API regressions, not a model benchmark. The runner replays the actual
preceding answers within the widget's six-turn/4,000-character history limits.

For the reported Pulsi-to-escalation regression, start the backend and run:

```bash
uv run python scripts/run_evals.py \
  --api-base http://localhost:8000 \
  --origin http://localhost:5173 \
  --cases evals/conversations.yaml \
  --case pulsi-escalation-sq --case pulsi-escalation-en
```

Omit `--case` to run all conversations. Each turn is reported separately, without
printing its question or answer. Automatic checks cover stream completion,
citation bounds/markers, forbidden text, widget formatting and requested sentence
limits. Semantic requirements and relevant-source selectors remain in each turn's
`manual_review`; passing structural checks is not a factual-accuracy score.

Review the original follow-up, explicit historical/foreign scope, relative dates,
language switches, legal-status distinctions, reference-only article limits and
forged-history recovery. In particular, neither map silence nor an attendance
index proves whether violence occurred, and chronology alone does not prove cause.

The standalone benchmark runner, model grid and benchmark-only tests were removed.
Their synthetic artifacts and recoverable tooling copies remain gitignored under
`.local/benchmarks/`. The production backend imports no experiment code.

## Local verification: 2026-10-18

All 232 Python tests, strict type checks, Ruff lint, changed-file formatting and
Git whitespace checks passed.

The Azure smoke test passed with the three configured runtime roles and unchanged
embeddings. Against the real HTTP backend, Azure models and active Frankfurt
Firestore corpus, 20 selected conversation turns passed automatic checks: the
Albanian/English Pulsi-to-escalation sequences, relative dates and explicit
historical corrections. These used process-local test quotas, not production
quota writes.

The original Albanian peak-to-escalation pair was also reviewed directly: the
peak retained its estimated-index caveat, and escalation retrieved October
reporting rather than treating the peak as evidence of violence. Reported causes
remained attributed, with no unsupported claim about who started a clash.

This is bounded local verification, not a pass of all 72 conversation questions
or a production deployment. The benchmark framework is not required by the app;
normal unit tests and API regressions remain the maintenance checks.
