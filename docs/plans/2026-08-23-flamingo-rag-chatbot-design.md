# Flamingo RAG Chatbot Design

- Status: Approved for dedicated-project Firestore provisioning and local
  end-to-end validation; production application deployment remains owner-controlled
- Date: 2026-08-23
- Scope: Local-first implementation followed by an explicitly approved GCP deployment
- Related governance: [`../constitution.md`](../constitution.md)

## Decision summary

The repository now contains a Python RAG service with a small,
framework-independent Svelte widget. The service ingests content from the three
local source repositories, generates
embeddings through the existing Azure OpenAI endpoint, and use Firestore Vector
Search in Frankfurt for production retrieval. The answer model is the existing
Azure `gpt-5.6-luna` deployment with medium reasoning effort.

Do not use LangChain, LangGraph, ChromaDB, or a live website crawler in the first
version. Direct SDK integrations keep the system smaller, easier to test, and
easier to operate. An orchestration framework can be reconsidered only if the
product later gains a real multi-step workflow that the direct implementation
cannot express cleanly.

The complete update and serving flow is:

```text
Versioned local repositories
        |
        v
Python ingestion CLI -> parse -> normalize -> chunk -> hash/diff
        |                                      |
        |                                      +-> dry-run report
        v
Azure embedding endpoint
        |
        v
Staged Firestore generation -> validation -> atomic current-pointer switch
        |
        v
FastAPI retrieval service -> Azure Luna -> cited streamed response
        |
        v
Embeddable Svelte web component on approved websites
```

## The 12-point plan

### 1. Product objective and boundaries

The chatbot has two explicit purposes:

1. Explain the Flamingo Revolution and Diaspora Zbarkon using the organizations'
   own published material and the dossier's structured evidence.
2. Demonstrate, transparently, that a useful public-information assistant can be
   built and operated with a small, auditable architecture.

It is an independent project, not an Albanian government service. The widget
must display that disclosure. It must not invent facts, present allegations as
judgments, or imply that its avatar is a live government representative.

The first milestone is local operation. Creating a GitHub repository, making
the initial commit, and authorizing a production deployment remain actions for
the repository owner.

### 2. Application stack and ownership boundaries

- Backend and ingestion: Python 3.12, managed with `uv` and `pyproject.toml`.
- HTTP service: FastAPI served by Uvicorn.
- Model access: direct Azure OpenAI-compatible SDK calls. `openai.yaml` contains
  only non-sensitive model behavior, while both the Azure API base and API key
  are supplied through environment variables.
- Local-integration and production vector store: Firestore Native with Vector
  Search.
- Frontend widget: Svelte, TypeScript, and Vite, exported as an embeddable web
  component or a similarly isolated single-script bundle.
- Production runtime: Cloud Run in `europe-west3` with minimum instances set to
  zero.

The ingestion library, retrieval library, API layer, and widget remain separate
modules. Model, embedding, and vector-store integrations sit behind narrow
project-owned interfaces so they can be tested without live services.

### 3. Knowledge sources and provenance

Ingestion reads only from updated local checkouts configured through the ignored
environment, never from live pages during a chat request:

- `flamingo-dossier`
- `flamingo-revolution-site`
- `diaspora_zbarkon`

The canonical dossier inputs are `data/dosje.csv` and `data/lidhje.csv` in the
dossier repository. Generated or copied representations, including the duplicate
`diaspora_zbarkon/data/scandals.ts`, are not ingested as a second source.

The protest participation series in `diaspora_zbarkon/data/participation.ts` is
structured data and is parsed as such rather than scraped for strings. Its values
are a normalized index produced by a crowd-counting model, not a headcount, so
every chunk derived from it carries that framing in its sticky context and its
recorded status. The /pulsi page copy is ingested alongside it so the
visitor-facing description of the method travels with the figures.

Every document and chunk records its repository, relative path, Git commit SHA,
content hash, source URL when available, language, content type, title, and any
relevant legal or publication status. Live URLs may be checked during ingestion
for validity, but live page content does not silently override the local source.

Diaspora Zbarkon's style can guide the assistant's rhetoric. Factual claims must
still come from retrieved, cited material rather than from tone instructions.

### 4. Python ingestion interface and update workflow

The update job is a Python CLI, not an npm task:

```bash
uv run python -m flamingo_bot.ingest --dry-run
uv run python -m flamingo_bot.ingest --publish
```

`--dry-run` scans the configured repositories, validates inputs, parses and
chunks content, reads the active Firestore generation, computes the exact change
set, and reports estimated embedding and Firestore work. It performs no
Firestore writes. An explicitly labeled `--local-only` mode exists for offline
parser/chunker work and does not claim an exact production diff.

`--publish` repeats validation, embeds only the required chunks, stages a new
generation, runs post-write checks, and switches the active generation only
after every check passes. The command emits a machine-readable ingestion report
with counts, source commits, skipped files, errors, and the resulting generation
identifier.

Repository paths, target project, database ID, and model deployment names are
configuration. They are never baked into parsing logic.

The implementation contains no concrete `api_base` value in `openai.yaml`.
Local development provides `AZURE_OPENAI_API_BASE` and
`AZURE_OPENAI_API_LLM_KEY` through `.env`; `.env.example` documents those
names without real values. Production will inject the equivalent variables from
Secret Manager. Startup must fail with a clear configuration error when either
required value is absent.

### 5. Parsing, normalization, and chunking

Parsers handle the source formats actually present in the repositories, such as
Markdown/MDX, structured CSV/JSON/TypeScript data, and page content. They remove
navigation, build artifacts, scripts, duplicate boilerplate, and presentation
markup that does not add meaning.

Chunking follows semantic boundaries:

- Target size: 500-700 tokens.
- Overlap: approximately 80 tokens when continuity requires it.
- Headings and source titles are carried into chunk text or metadata.
- A claim, its named parties, source, date, and legal status stay together.
- Tables and structured dossier rows are rendered into readable, deterministic
  text rather than split by arbitrary character counts.

Chunk IDs are deterministic. Normalized text and metadata are hashed so an
unchanged chunk is recognizable across ingestion runs.

### 6. Embeddings and incremental change detection

Use the existing Azure `text-embedding-3-large` deployment with 1,024 output
dimensions. Embedding calls are batched within the endpoint's limits and use
finite timeouts plus bounded retries for transient failures.

For each new ingestion generation:

- Unchanged content reuses its existing embedding.
- New or changed content receives a new embedding.
- Removed content is omitted from the new generation.
- A change to the embedding model, dimensions, normalization contract, or
  chunking version forces an intentional rebuild.

The generation manifest records the embedding deployment identifier, dimensions,
chunker version, parser version, source commits, and counts. Secret values and
endpoint credentials never enter the manifest or logs.

### 7. Firestore Vector Search layout and atomic publication

Create a new Firestore Native database with these fixed choices:

- Database ID: `flamingo-rag`
- Location: `europe-west3` (Frankfurt)
- Delete protection: enabled
- Vector field: `embedding`
- Source-filter field: `source_id`
- Dimensions: 1,024
- Distance measure: cosine

Two flat vector indexes support this contract: the base `embedding` index and a
compound `source_id` plus `embedding` index. The second index lets retrieval
collect a small number of relevant hits from each approved source before final
diversification; it does not increase the final evidence budget.

The logical layout is:

```text
rag_generations/{generation_id}
rag_generations/{generation_id}/chunks/{chunk_id}
rag_meta/current
ingestion_runs/{run_id}
rollback_runs/{rollback_id}
```

A publish writes to a new immutable generation. It validates document counts,
vector dimensions, metadata, representative searches, and source coverage, then
updates `rag_meta/current` in a transaction. Readers resolve that pointer once
per request. A failed staging run cannot corrupt the currently served data.

Keep at least the current and immediately previous valid generation. Rollback is
a pointer change. Cleanup of older generations is bounded, separately reported,
and only happens after the rollback window. Firestore database delete protection
does not replace document-level retention or backup discipline.

### 8. Retrieval and answer-generation contract

For each accepted question, the backend:

1. Normalizes the request and applies abuse and size limits.
2. Rewrites a follow-up into a standalone query when the request replays earlier
   turns, as described under conversation memory below.
3. Creates a query embedding using the same embedding contract as ingestion.
4. Searches only the active Firestore generation.
5. Runs source-filtered vector lookups, takes at most three hits per approved
   source, applies the evidence-calibrated `0.65` cosine-distance threshold, and
   diversifies to at most 5-7 final candidates by source and topic.
6. Sends the best evidence to the existing Azure `gpt-5.6-luna` deployment with
   `reasoning.effort: medium`.
7. Streams the answer together with stable citations to the source material.

Retrieved text is evidence, not executable instruction. The model prompt directs
the assistant to ignore instructions embedded in source documents, distinguish
claims from established findings, and say when the available evidence cannot
answer a question. Citations must support the sentence or paragraph to which
they are attached.

The prompt also fixes the answer's formatting to exactly what the widget
renders: paragraphs, bullet and numbered lists, bold, and italic. Headings,
tables, code fences, blockquotes, and Markdown links are excluded, so the two
sides cannot drift into a visitor reading raw `**` markers on screen.

#### Conversation memory

A visitor asking "say that in two sentences" or "what about the Vlora airport?"
means the previous turn. Retrieval cannot resolve that from the latest message
alone, so the request carries the recent turns with it.

The service stores no conversation. The widget replays turns from the browser
tab it holds them in, which keeps the deployment stateless and avoids the
retention, access, and deletion obligations that Article VI of the constitution
attaches to conversation storage. Nothing is written to browser storage either,
so a reload starts a new conversation, and the visitor can clear the transcript
at any time from the widget.

Because the turns arrive from the client they are unverified input, not
evidence, and are bounded before use:

- At most `FLAMINGO_HISTORY_TURNS` turns (default 6) within
  `FLAMINGO_HISTORY_CHARS` characters (default 4,000). The oldest kept turn is
  truncated to fit rather than the newest being dropped.
- The transport rejects an unknown role, a turn over 2,000 characters, or more
  than 16 turns.
- Evidence markers such as `[S1]` are stripped from replayed answers, because
  those markers referred to evidence retrieved for an earlier question.
- Setting `FLAMINGO_HISTORY_TURNS=0` disables the feature and restores
  single-question behavior without a code change.

When turns are present, a short non-streaming `condense` call rewrites the
latest message into one standalone search query. It uses the same deployment
with `reasoning.effort: none`, measured on 2026-08-23 as matching `low` on
rewrite quality at about half the latency. The call is best-effort: a provider
failure, or output that is empty or over 400 characters, falls back to a
deterministic query that pairs the latest message with the previous visitor
question, so conversation memory can never fail a chat request. A first question
is already standalone and skips the call entirely.

The grounded prompt receives the transcript in a delimited block labelled as
unverified context. The system instructions state that it may only resolve
references and keep the visitor's language, that it can never support a factual
claim or override the grounding rules, and that only markers supplied with the
current question may be cited. A forged assistant turn is therefore inert:
the evaluation set pins this with a case that plants a fake credential in the
replayed history and requires that the answer never repeats it.

The assistant should answer in the visitor's language when practical, while
engineering documentation, code, identifiers, comments, and operator messages
remain in English. Original source names and excerpts may retain their source
language.

### 9. Python API and operational behavior

The FastAPI service exposes a small versioned contract, initially including:

- `GET /health` for process liveness.
- `GET /status` for dependency readiness and active-generation metadata without
  exposing secrets.
- `POST /v1/chat` for a server-sent-events response containing answer deltas,
  citations, completion state, and structured errors. The request body carries
  the question and an optional bounded `history` array of `{role, text}` turns.

The API validates request bodies, limits concurrent and oversized work, applies
an explicit website-origin allowlist, and supports rate limiting. All outbound
calls have deadlines. Retries occur only where the operation is idempotent.

Structured logs contain request IDs, latency, retrieval counts, replayed-turn
counts, model usage, and error classes. Raw visitor questions, full answers,
replayed conversation turns, credentials, embeddings, and sensitive source
content are not logged by default.

### 10. Embeddable widget and avatar media

The Svelte/TypeScript widget is distributed independently of the host site's
framework. It isolates styles, accepts a small configuration object, works on
mobile screens, supports keyboard and screen-reader use, and renders clickable
source citations. It clearly labels the assistant as an independent Flamingo
project.

The assistant is presented as "Diella - Flamingo Style", a deliberate reference
to the Albanian government's own AI program. Because the name invites the
comparison, the disclosure carries real weight: it must stay visible above the
transcript and must keep stating that this is not a government service or a live
representative. The welcome message is bilingual, Albanian first.

An answer arrives as the light Markdown subset the prompt allows. The widget
parses it into a node tree and renders real paragraphs, lists, `strong`, and
`em`. It never passes model or evidence text to `innerHTML`, so a source
document cannot introduce markup into the host page, and anything outside the
subset stays visible as the literal characters the model wrote. While an answer
is still streaming, an emphasis marker whose partner has not arrived yet styles
the tail rather than showing a bare `**` that vanishes a moment later.

The widget holds the conversation in the open tab and replays the recent turns
with each question. It never writes them to browser storage, excludes its own
welcome copy and any cancelled or failed exchange from what it replays, states
in the disclosure that the conversation stays in the browser, and offers a
control that clears the transcript and starts a new conversation.

The requested avatar treatment is a self-hosted, optimized 10-second silent loop
derived from project-authorized media. Exact provenance and authorization
details remain in the private rights record. The loop will not use source audio.

For the authorized asset, provide MP4 and WebM variants, a poster image, `muted`,
`loop`, and `playsinline` behavior. Load and play the video only while the widget
is open. Visitors who prefer reduced motion receive the static poster. The widget
must not imply live presence, official government affiliation, or endorsement.

### 11. Local development, evaluation, and acceptance tests

Local commands remain split by responsibility:

```bash
# Backend
uv sync --dev
uv run uvicorn flamingo_bot.api:app --reload --port 8000

# Frontend
npm --prefix frontend install
npm --prefix frontend run dev

# Ingestion
uv run python -m flamingo_bot.ingest --dry-run
uv run python -m flamingo_bot.ingest --publish
```

The localhost API uses the same `flamingo-rag` Firestore database and active
generation as the deployed service. The local application identity is read-only:
it may resolve `rag_meta/current` and retrieve chunks, but it cannot publish,
switch, or delete generations. Only the separately authenticated Python
`--publish` workflow receives write access. This keeps one retrieval backend and
does not justify adding ChromaDB or another local vector datastore. Unit tests
continue to use in-memory fakes and make no billable cloud calls.

Required verification includes parser fixtures, deterministic chunking and IDs,
incremental diff behavior, embedding and Firestore adapters, staged publication
and rollback, API streaming, citations, legal-status wording, multilingual
questions, irrelevant questions, prompt-injection attempts, rate/origin limits,
mobile layout, accessibility, reduced motion, and failure paths. Live Azure/GCP
tests are explicitly marked and never required for the default unit suite.

An evaluation set should include representative Flamingo Revolution and Diaspora
Zbarkon questions, questions whose answers span sources, and unanswerable or
adversarial questions. A release fails if it loses source attribution or turns
an allegation into an unsupported conclusion.

### 12. GCP security, billing, deployment, and rollback

Production resources are co-located in Frankfurt:

- Firestore Native database `flamingo-rag` in `europe-west3`.
- Cloud Run service in `europe-west3`, minimum instances `0`, with a conservative
  maximum-instance limit.
- Artifact Registry repository in `europe-west3`.
- Secret Manager secrets with replication constrained to the intended European
  region where the selected secret type permits it.

Use separate least-privilege identities for provisioning, local ingestion,
Cloud Run runtime, and GitHub deployment. Do not reuse credentials from an
unrelated project. Prefer user authentication plus service-account impersonation
for local ingestion; if a dedicated key is unavoidable, it stays outside every
repository and is rotated. Cloud Run can read only its named Azure secret and
the required Firestore data.

Use a dedicated project linked to the owner-approved billing account. Exact
account identifiers and credit-program details remain in the private operator
environment. The infrastructure verifier checks the exact billing link using
the private runtime value but does not claim that a resource can be assigned to
an individual credit. Budgets and alerts are added for visibility but are not
treated as hard spending caps. Azure model usage is tracked separately because
GCP credits do not cover it.

The repository contains CI and manual deployment workflows. After the owner
creates the GitHub repository, makes the initial commit, configures protected
environment variables, and approves deployment, the GitHub Actions workflow can:

1. Run tests and build the application.
2. Authenticate to GCP with Workload Identity Federation, without a GitHub
   service-account key.
3. Build the container.
4. Push it to the Frankfurt Artifact Registry repository.
5. Deploy a new Cloud Run revision in `europe-west3`.
6. Run health and retrieval smoke tests.
7. Preserve the last known-good revision and shift traffic back on failure.

The owner authorized creation of the dedicated project, Firestore resources,
least-privilege local identities, and the first data publication before the
initial commit. Git initialization, the initial commit, remote creation, and the
production application deployment remain owner-controlled.

## Research-backed infrastructure notes

- A Firestore database location cannot be changed after provisioning, which is
  why Frankfurt is an explicit pre-creation decision:
  <https://firebase.google.com/docs/firestore/locations>.
- Firestore supports K-nearest-neighbor vector search and cosine distance with a
  configured vector index:
  <https://firebase.google.com/docs/firestore/vector-search>.
- Cloud Run is available in `europe-west3`, and services can scale to zero:
  <https://cloud.google.com/run/docs/locations> and
  <https://cloud.google.com/run/docs/overview/what-is-cloud-run>.
- Cloud Billing budgets provide alerts and automation hooks but do not cap
  charges automatically:
  <https://cloud.google.com/billing/docs/how-to/budgets>.

## Implementation status on 2026-08-23

Completed through the Firestore-first local milestone:

- Python package, typed configuration, locked dependencies, parsers, provenance,
  deterministic chunking, incremental embedding contract, dry-run, Firestore
  adapter, staged publication logic, validated transactional data rollback,
  retrieval, grounding, and SSE API.
- Svelte custom element, responsive/accessibility behavior, citation rendering,
  independent-project disclosure, and authorized 10-second silent avatar media.
- Live Azure embedding and Luna streaming smoke check.
- Multi-stage non-root production image, local container/static-asset smoke test,
  CI workflow, manual WIF deployment workflow, runbooks, and evaluation set.
- Dedicated project linked to the approved billing account;
  Frankfurt Firestore with delete protection and both required indexes `READY`.
- First immutable generation `20260823T054709Z-ff6624366ee5`, containing 850
  chunks from 94 documents with zero failed or skipped files.
- Exact post-publication diff proving all 850 embeddings reusable with zero
  new/removed chunks.
- Twelve-case live automatic evaluation, content-level agent review, and real
  headless Chrome widget/SSE check at desktop and 390×844 mobile size. Owner
  human disposition remains required before release.

Still owner-controlled:

- Create the GitHub repository and make the initial commit.
- Provision production-only delivery resources and execute the first Cloud Run
  deployment after the initial commit.

## Definition of done for the local milestone

- The widget and API run on localhost. **Verified end to end against the live
  Firestore generation and Azure endpoints.**
- The Python dry run scans all three repositories and reports the proposed
  Firestore change set without writing it.
- An explicit publish creates a validated Firestore generation, and the
  read-only localhost API retrieves from it. **Verified with active generation
  `20260823T094238Z-002c3ce5269a`.**
- Representative questions produce source-grounded answers with citations.
  **All twelve versioned live cases passed and agent content review found no
  contract violation; owner human disposition remains pending.**
- Unanswerable and adversarial questions fail safely. **Covered at unit/contract
  level and verified live.**
- No secrets, raw embeddings, generated indexes, unauthorized third-party media,
  or credentials are tracked.
- Unit, accessibility, Azure-provider, widget, and container smoke checks pass.
  **Live Firestore-backed and browser evaluation also pass.**
- The owner can review all files before creating the repository and initial
  commit.
