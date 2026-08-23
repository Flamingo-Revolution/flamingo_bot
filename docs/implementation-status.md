# Implementation status

- Evidence date: 2026-08-23 (conversation memory added the same day)
- Design: [`plans/2026-08-23-flamingo-rag-chatbot-design.md`](plans/2026-08-23-flamingo-rag-chatbot-design.md)
- Overall state: dedicated Firestore data plane, localhost application, and
  production Cloud Run release are verified end to end; host-site integration
  and owner content disposition remain

This document distinguishes executable local evidence from mutable cloud state.
It is not a production-readiness declaration.

| Design point | Current evidence | Remaining gate |
| --- | --- | --- |
| 1. Objective and boundaries | Grounded prompt, widget disclosure, source-only refusal, and legal-status evaluation cases are implemented. A content-level agent review found the live rhetoric, affiliation disclosure, and legal-status handling consistent with the contract. | Owner human review and disposition before release; re-review after material source or prompt changes. |
| 2. Stack and boundaries | Python 3.12/FastAPI backend, direct Azure SDK adapters, live Frankfurt Firestore, Svelte custom element, Docker image, and Frankfurt Cloud Run service exist. No LangChain, LangGraph, or ChromaDB is present. | Integrate the deployed widget into staging copies of the approved host sites. |
| 3. Sources and provenance | Three local Git repositories are allowlisted. Parsers retain repository, path/record, revision, hash, URL, language, type, title, and legal metadata; known duplicates and presentation-only files are excluded. | Review future source-schema changes before publishing. |
| 4. Ingestion workflow | Publish activated `20260823T094238Z-002c3ce5269a` with 884 chunks. The pre-deployment dry-run found 44 files, 98 documents, zero failures/skips, zero additions/removals, and all 884 embeddings reusable, so no redundant generation was published. | Run the same reviewed workflow after future source updates. |
| 5. Parsing and chunking | Format-specific fixtures and deterministic semantic chunk tests pass. Current chunks average about 530 tokens, max 677, with none above the 700-token ceiling. Live retrieval evaluations passed over this corpus. | Review future parser/source-schema changes. |
| 6. Embeddings and diff | Azure `text-embedding-3-large`, 1,024 dimensions, batching, contract-version rebuilds, hash reuse, and duplicate-aware removals are implemented. The first publish embedded 850 chunks; adding the participation parser re-embedded only the 51 chunks it changed and reused the other 833. | Monitor subsequent incremental counts and Azure usage. |
| 7. Firestore publication | Firestore Native `flamingo-rag` is live in `europe-west3` with delete protection. Both flat 1,024-dimensional indexes are `READY`; the active generation passed count, source-coverage, and per-source vector checks. One pre-activation serialization failure is retained as a `failed` audit record and never changed the pointer. | Add a second retained valid generation before exercising live rollback. |
| 8. Retrieval and Luna | Source-filtered cosine search takes at most three hits per source, applies the measured `0.65` threshold, diversifies to at most seven chunks, and invokes `gpt-5.6-luna` with medium reasoning. A follow-up is first rewritten into a standalone query by a bounded `condense` call with a deterministic fallback. The prompt limits answer formatting to the Markdown subset the widget renders. All twelve automatic cases pass locally and against the promoted production revision, including required Revolution plus Diaspora coverage, refusal, injection resistance, legal certainty, follow-up reference, mid-conversation topic switch, forged replayed history, and the participation index with its measurement method. | Owner human disposition, then re-evaluate after material corpus, threshold, embedding, or prompt changes. |
| 9. API operations | `/health`, `/status`, SSE `/v1/chat`, request bounds, origin guard, CORS, concurrency/rate limits, safe error events, hashed safety ID, timeouts, and non-content operational logs exist. Production chat requires an allowed Origin, and rate limiting uses the proxy-appended client address rather than the load-balancer address or an untrusted prefix. The service stores no conversation. | Monitor production telemetry and revisit edge enforcement with Cloud Armor if public usage warrants a load balancer. |
| 10. Widget and avatar | Framework-independent responsive custom element, citations, keyboard/focus behavior, live region, cancellation, reduced-motion poster, and authorized silent 10-second MP4/WebM loop are built. It replays tab-local conversation turns, writes nothing to browser storage, and offers a new-conversation control. Answers are rendered as real elements, never through `innerHTML`. The same widget also powers a responsive standalone page at the service root and opens there automatically. | Integrate into staging copies of the real host sites and repeat browser review there. |
| 11. Tests and evaluation | 95 Python tests and 19 widget tests pass; strict mypy, Ruff, Svelte check, frontend build, local production-image build, exact no-change source diff, the workflow candidate smoke, the full twelve-case local and production evaluations, and browser review at desktop and phone widths have passed. | Record the owner's human content disposition and repeat browser review after host-site integration. |
| 12. Security, cost, deploy, rollback | The complete infrastructure verifier passes billing linkage, Frankfurt Firestore/indexes, Artifact Registry, Cloud Run/public invoker, all three regional secrets, and both custom roles. Runtime, ingestion, and keyless GitHub deployment identities are separate and least-privilege; WIF is restricted to immutable repository/owner IDs, `main`, `production`, and the exact workflow. Cloud Run scales from zero to at most five instances. A project-filtered gross-usage budget alerts before credits hide consumption. The protected workflow proved zero-traffic candidate smoke and promotion while retaining the prior revision. | Review the three pre-existing GitHub admin accounts when the owner is ready; monitor GCP and Azure usage separately. |

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
uv run --env-file .env python scripts/verify_gcp_contract.py --pretty
/usr/bin/docker build -t flamingo-bot:local .
```
