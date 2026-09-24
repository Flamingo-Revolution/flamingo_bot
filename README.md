# Flamingo Bot

Flamingo Bot is an independent, source-grounded RAG assistant for Flamingo
Revolution, its protest map, Flamingo News, and Diaspora Zbarkon. It uses
versioned content, Azure embeddings and Luna responses, Firestore Vector Search,
a FastAPI SSE service,
and an embeddable Svelte custom element.

The dedicated GCP data plane, localhost application, and production Cloud Run
service are verified end to end. Firestore generation
`20260924T155907Z-278566f3e1c0` is active in Frankfurt with 1,549 source-grounded
chunks. Local and production APIs have passed the full live evaluation suite,
and the production widget bundle is available for host-site integration.

## Architecture

```text
local source repos -> Python parse/chunk/diff -> Azure embeddings
                                            -> staged Firestore generation
                                            -> active pointer

visitor -> <flamingo-chat> -> FastAPI SSE -> Firestore cosine search
                                         -> Azure gpt-5.6-luna
                                         -> cited streamed answer
```

There is no LangChain, LangGraph, ChromaDB, live request-time crawler, or second
local vector database. Unit tests use non-billable in-memory fakes; local and
production retrieval use the same named Firestore database.

Live retrieval uses a base vector index plus a `source_id`-filtered vector index
to prevent a larger source from dominating cross-source questions. At most three
hits per source are considered and no more than seven diversified chunks reach
Luna.

Answers use a small Markdown subset: paragraphs, bullet and numbered lists,
bold, and italic. The prompt allows nothing else and the widget renders exactly
that set as real elements, never through `innerHTML`, so the two must be changed
together.

## Conversation memory

The assistant understands follow-ups such as "say that in two sentences" or
"what about the Vlora airport?". The service stays stateless: the widget holds
the conversation in the open browser tab and replays the recent turns with each
request, so nothing is stored server-side and nothing is written to browser
storage. A reload starts fresh, and the widget offers a control that clears the
transcript.

Replayed turns are treated as unverified client input, never as evidence. They
are capped by `FLAMINGO_HISTORY_TURNS` (default 6) and `FLAMINGO_HISTORY_CHARS`
(default 4,000), stale `[S1]`-style markers are stripped from them, and
`FLAMINGO_HISTORY_TURNS=0` disables the feature. When turns are present a short
`condense` model call rewrites the question into a standalone search query, with
a deterministic fallback so this step can never fail a chat request.

## Requirements

- Python 3.12 and `uv` 0.11.8
- Node.js 24 and npm
- Docker for production-image checks
- Updated local checkouts of the three approved content repositories
- Azure values in ignored `.env` for live model calls
- User ADC as the source for project-local service-account impersonation

Both `AZURE_OPENAI_API_BASE` and `AZURE_OPENAI_API_LLM_KEY` are runtime
environment variables. `openai.yaml` contains only non-sensitive model behavior.

## Local setup and checks

```bash
uv sync --locked --dev
uv run pytest
uv run ruff check .
uv run mypy src scripts

npm --prefix frontend install
npm --prefix frontend run check
npm --prefix frontend test
npm --prefix frontend run build
```

Run the API and widget preview in separate terminals:

```bash
uv run uvicorn flamingo_bot.api:app --reload --port 8000
npm --prefix frontend run dev
```

The API exposes the standalone chat at `GET /`, operational endpoints at
`GET /health` and `GET /status`, streaming chat at `POST /v1/chat`, and—after a
widget build—static integration assets under `/widget`. The production embed is:

```html
<script type="module" src="https://BOT_HOST/widget/flamingo-chat.js"></script>
<flamingo-chat></flamingo-chat>
```

See the [widget runbook](docs/runbooks/widget-integration.md) for cross-origin
configuration and optional attributes.

## Knowledge sources and ingestion

### Where the corpus comes from

The ingestion command takes no path arguments. What to read is declarative: it
resolves the source catalog, then the repository path each catalog entry names.

1. `Settings()` reads `.env` and the environment.
2. `settings.source_catalog_path` points at [`config/sources.yaml`](config/sources.yaml).
3. Each catalog entry carries a `repository_setting` **name**, not a path.
4. `resolve_sources()` looks that name up on `Settings` to get the checkout.
5. `repository_revision()` records `git rev-parse HEAD` for the checkout and
   refuses one whose `git status --porcelain` is not empty.
6. Each rule's `include` and `exclude` globs run relative to that checkout.

| Source id | Repository setting | Ingested content |
| --- | --- | --- |
| `flamingo-dossier` | `FLAMINGO_DOSSIER_REPO` | `data/dosje.csv`, with entity relations joined from `data/lidhje.csv` |
| `flamingo-revolution` | `FLAMINGO_REVOLUTION_REPO` | `public/llms.txt`, blog and page Markdown, selected `src/data/*.ts`, public PDFs including Flamingo Times, and published Flamingo News articles fetched from the public site |
| `diaspora-zbarkon` | `DIASPORA_ZBARKON_REPO` | `specs/Mission.md`, `lib/content.ts`, the /pulsi page copy, and the protest participation series |
| `flamingo-revolution` (map) | `FLAMINGO_MAP_REPO` | `data/locations.json`: one document per city and protest, plus map-wide totals; cites `/harta/` |

The dossier, Revolution, Diaspora, and map content is read from clean local
checkouts. Their Git revisions are recorded in the generation manifest. Flamingo
News is fetched from the public `/news/` listing on each run; only published
article pages enter the corpus. Two dated, one-time snapshots are stored under
`content/snapshots/`: 17 public informational pages of Referendum 21/2024 and
the author's Pulsi analysis originally posted on Reddit, split into two topic
records for retrieval. Routine ingestion reads these snapshots, but does not
refresh either external site. Referendum
pages can be intentionally recaptured with `uv run python
scripts/snapshot_referendum21.py` and reviewed before the next publication.

### Refreshing existing data

Pull the source repository, then re-run ingestion. Ingestion never pulls for
you, because the SHA it stamps into the manifest has to be a commit that exists
upstream. Set `GCP_INGESTION_SERVICE_ACCOUNT` in the private operator shell to
the dedicated ingestion identity before publishing.

```bash
git -C /path/to/source-repo pull
uv run python -m flamingo_bot.ingest --dry-run --pretty
GCP_IMPERSONATE_SERVICE_ACCOUNT="$GCP_INGESTION_SERVICE_ACCOUNT" \
  uv run python -m flamingo_bot.ingest --publish --pretty
```

The dry run reads the active manifest, hashes, and vectors, so its
new/reused/removed counts are exact rather than estimated. Review them before
publishing. Publish embeds only chunks whose content hash is not already in the
active generation, stages a complete new generation, and switches the pointer
transactionally, so editing one dossier row re-embeds a handful of chunks rather
than the whole corpus.

Leave the source checkout clean. A pull that leaves conflicts, build output, or
stray untracked files makes `repository_revision()` raise
`SourceValidationError` and blocks the run.

For parser or chunker work without Google credentials or network access, use
`--dry-run --local-only`. That mode labels its report `local-source-only` and
cannot claim an exact diff against production.

The 2026-08-23 baseline was 44 files, 98 documents, and 884 chunks. Counts change
as the sources grow. To undo a publication, see the rollback command below.

### Adding a new source

A new source enters at the front of the pipeline, in the catalog. Everything
after parsing is generic: chunking, hashing, embedding, staging, and the pointer
switch need no changes. Four places do.

1. **[`config/sources.yaml`](config/sources.yaml)**: add an entry with `id`,
   `label`, `language`, `base_url`, and its parser `rules`. The `label` is
   visitor-visible in the widget's citation list.
2. **`src/flamingo_bot/config.py`**: add an optional `Path` field on `Settings`
   named by that entry's `repository_setting`, add it to the optional-path
   validator, and document the variable in `.env.example`. `resolve_sources()`
   raises `ConfigurationError` when the field is absent or unset.
3. **`src/flamingo_bot/retrieval.py`, `_SOURCE_IDS`**: add the id if it is new.
   The map deliberately shares the Revolution retrieval id while recording its
   own `revision_key`, so it can be retrieved without a Cloud Run redeploy. `retrieve()`
   issues one filtered vector query per id in that tuple and issues no
   unfiltered query, so a source missing from it is parsed, chunked, embedded,
   billed, and stored, and then never retrieved. Nothing fails and no error is
   logged; answers are simply missing that source.
4. **`src/flamingo_bot/parsers.py`**: only if the format is new. Markdown,
   plain text, PDF, and TypeScript string extraction already exist in
   `_parse_generic`. A structured format gets its own function dispatched in
   `parse_sources`, the way `dossier_csv` is. Add a `_canonical_url` branch too,
   or every citation from the source links at the bare `base_url` instead of the
   page that supports the claim.

Then add an evaluation case. `evals/questions.yaml` pins cross-source coverage
against the existing labels, so a new source is otherwise unmeasured.

Two behaviors to expect:

- **Publish refuses if any catalogued source contributes zero documents.** A
  glob that matches nothing fails the whole run rather than quietly shipping a
  partial corpus. The dry run does not fail on it; it only shows the lower
  counts, so read `source_revisions` and the counts before publishing.
- **Do not bump `PARSER_VERSION`, `CHUNKER_VERSION`, or
  `NORMALIZATION_VERSION` casually.** They are part of the embedding reuse
  contract, so `manifest_supports_reuse()` rejects the active generation's
  vectors and every chunk is re-embedded at cost. Adding a new parser function
  alongside the existing ones does not require a bump; changing how an existing
  format is normalized does.

See the [ingestion runbook](docs/runbooks/ingestion.md) for the publish
identity, failure handling, and the audit records each run writes.

## Live checks and evaluation

The Azure smoke script prints only vector dimensions and response length:

```bash
uv run python scripts/smoke_azure.py
```

On 2026-08-23 it verified a 1,024-dimensional embedding and a successful Luna
stream. It did not print content, vectors, endpoint, or credentials.

Run the versioned evaluation set against the active generation:

```bash
uv run python scripts/run_evals.py
```

Automatic results never print questions or answers and still require the human
review described in the [evaluation runbook](docs/runbooks/evaluation.md).

Knowledge rollback is an explicit Python operation. It revalidates retained
chunk count and vector lookup before the transactional pointer switch:

```bash
uv run python -m flamingo_bot.rollback --previous --pretty
uv run python -m flamingo_bot.rollback --to GENERATION_ID --pretty
```

## Production boundary

Firestore Native `flamingo-rag`, Artifact Registry, regionally replicated
secrets, and the Cloud Run service are live in `europe-west3`. The container uses
a non-root runtime, scales to zero with a service maximum of five instances, and
includes the widget plus authorized silent avatar media. CI and the protected,
keyless WIF deployment workflow are active. The
machine-readable [`infra/contract.yaml`](infra/contract.yaml) and its default
tests prevent regional, vector, IAM, bootstrap, and zero-traffic release settings
from drifting silently.

The approved target is a dedicated owner-configured project linked to the
owner-confirmed billing account. Exact billing identifiers stay in the ignored
`.env` and secure deployment variables. Follow the [GCP deployment
runbook](docs/runbooks/gcp-deployment.md) for the staged workflow.

After provisioning and deployment, `scripts/verify_gcp_contract.py` compares the
project supplied with `--project` or `GCP_PROJECT_ID`, the billing account
supplied through `GCP_BILLING_ACCOUNT`, and the resource shape in
`infra/contract.yaml`. The complete production contract currently passes.

The approved [design](docs/plans/2026-08-23-flamingo-rag-chatbot-design.md) and
[constitution](docs/constitution.md) are normative. The [implementation status](docs/implementation-status.md)
maps evidence and remaining gates to all 12 design points. Engineering
documentation, code, identifiers, comments, and operator output are English;
source material and visitor answers may use their original/requested language.
