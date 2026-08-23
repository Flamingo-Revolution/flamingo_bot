# Implementation status

- Evidence date: 2026-08-23 (conversation memory added the same day)
- Design: [`plans/2026-08-23-flamingo-rag-chatbot-design.md`](plans/2026-08-23-flamingo-rag-chatbot-design.md)
- Overall state: dedicated Firestore data plane and localhost application are
  verified end to end; production application deployment remains deferred

This document distinguishes executable local evidence from mutable cloud state.
It is not a production-readiness declaration.

| Design point | Current evidence | Remaining gate |
| --- | --- | --- |
| 1. Objective and boundaries | Grounded prompt, widget disclosure, source-only refusal, and legal-status evaluation cases are implemented. A content-level agent review found the live rhetoric, affiliation disclosure, and legal-status handling consistent with the contract. | Owner human review and disposition before release; re-review after material source or prompt changes. |
| 2. Stack and boundaries | Python 3.12/FastAPI backend, direct Azure SDK adapters, live Frankfurt Firestore, Svelte custom element, Docker image, and Frankfurt Cloud Run workflow exist. No LangChain, LangGraph, or ChromaDB is present. | Deploy the application to Cloud Run after the owner's initial commit. |
| 3. Sources and provenance | Three local Git repositories are allowlisted. Parsers retain repository, path/record, revision, hash, URL, language, type, title, and legal metadata; known duplicates and presentation-only files are excluded. | Review future source-schema changes before publishing. |
| 4. Ingestion workflow | The exact dry-run found 44 files, 98 documents, and 884 chunks with no failures/skips. Publish activated `20260823T094238Z-002c3ce5269a`, embedding only the 51 changed chunks and reusing 833. | Run the same reviewed workflow after future source updates. |
| 5. Parsing and chunking | Format-specific fixtures and deterministic semantic chunk tests pass. Current chunks average about 530 tokens, max 677, with none above the 700-token ceiling. Live retrieval evaluations passed over this corpus. | Review future parser/source-schema changes. |
| 6. Embeddings and diff | Azure `text-embedding-3-large`, 1,024 dimensions, batching, contract-version rebuilds, hash reuse, and duplicate-aware removals are implemented. The first publish embedded 850 chunks; adding the participation parser re-embedded only the 51 chunks it changed and reused the other 833. | Monitor subsequent incremental counts and Azure usage. |
| 7. Firestore publication | Firestore Native `flamingo-rag` is live in `europe-west3` with delete protection. Both flat 1,024-dimensional indexes are `READY`; the active generation passed count, source-coverage, and per-source vector checks. One pre-activation serialization failure is retained as a `failed` audit record and never changed the pointer. | Add a second retained valid generation before exercising live rollback. |
| 8. Retrieval and Luna | Source-filtered cosine search takes at most three hits per source, applies the measured `0.65` threshold, diversifies to at most seven chunks, and invokes `gpt-5.6-luna` with medium reasoning. A follow-up is first rewritten into a standalone query by a bounded `condense` call with a deterministic fallback. The prompt limits answer formatting to the Markdown subset the widget renders. All twelve live automatic cases passed, including required Revolution plus Diaspora coverage, refusal, injection resistance, legal certainty, follow-up reference, mid-conversation topic switch, forged replayed history, and the participation index with its measurement method. Manual review confirmed the index is reported as an estimate and a direct "how many people" question is answered without inventing a headcount. | Owner human disposition, then re-evaluate after material corpus, threshold, embedding, or prompt changes. |
| 9. API operations | `/health`, `/status`, SSE `/v1/chat`, request bounds, origin guard, CORS, concurrency/rate limits, safe error events, hashed safety ID, timeouts, and non-content operational logs exist. `/v1/chat` accepts a bounded optional `history` array; the service stores no conversation and logs only the replayed-turn count. | Validate Cloud Run proxy/rate behavior and production telemetry. |
| 10. Widget and avatar | Framework-independent responsive custom element, citations, keyboard/focus behavior, live region, cancellation, reduced-motion poster, and authorized silent 10-second MP4/WebM loop are built. It replays tab-local conversation turns, writes nothing to browser storage, and offers a new-conversation control. Answers are parsed into paragraphs, lists, `strong`, and `em` and rendered as real elements, never through `innerHTML`. It is presented as "Diella - Flamingo Style" with a bilingual Albanian-first welcome; the header was verified at 1280, 390, and 320 pixels wide. Headless Chrome completed a real two-turn exchange, confirmed the replayed payload, confirmed the reset clears it, rendered a live bold bullet-list answer with no leftover `**` markers, and reported no console error or failed request at desktop and 390×844 mobile size. | Integrate into a staging copy of the real host site after deployment. |
| 11. Tests and evaluation | 91 Python tests and 19 widget tests pass; strict mypy, Ruff, Svelte check, frontend build, Azure smoke including the condensation call, exact source diff of 44 files/98 documents/884 chunks with 833 reused embeddings, twelve-case live evaluation, and browser E2E have passed. | Re-run the Docker build/smoke; record the owner's human disposition; repeat production smoke and browser review against the Cloud Run candidate. |
| 12. Security, cost, deploy, rollback | A dedicated project is linked to the owner-approved billing account; exact identifiers remain private. Separate impersonated runtime/read-only and deletion-free ingestion identities are database-conditioned; global ADC was not replaced. The verifier passes billing, database, both indexes, and ingestion role. | The full verifier correctly fails for intentionally absent Artifact Registry, Secret Manager, Cloud Run, public invoker, and deployer role. The owner must create the repository and initial commit before those production resources. |

## Verified commands

```bash
uv run pytest
uv run ruff check .
uv run mypy src scripts
npm --prefix frontend run check
npm --prefix frontend test
npm --prefix frontend run build
uv run python -m flamingo_bot.ingest --dry-run --pretty
uv run python scripts/run_evals.py
uv run python scripts/smoke_azure.py
```

Not re-run since conversation memory was added, because the container contents
did not change apart from the widget bundle and the Python package:

```bash
/usr/bin/docker build -t flamingo-bot:local .
```

The full infrastructure verifier is intentionally not green yet: its remaining
failures are the production-only delivery resources deferred until after the
owner's initial commit.
