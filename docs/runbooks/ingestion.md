# Ingestion runbook

## Source update workflow

1. Update the four approved local repositories (Dossier, Revolution, Diaspora,
   and Harta e Protestave) with their normal Git workflow.
2. Inspect their changes and commit them. Ingestion never performs `git pull` and
   refuses a repository with tracked or untracked working-tree changes.
3. Run the dry-run and review counts, revisions, skips, and failures.
4. Publish only with the dedicated GCP ingestion identity.
5. Verify the active generation and representative searches.

Set `GCP_INGESTION_SERVICE_ACCOUNT` in the private operator shell to the
dedicated ingestion identity before publishing.

```bash
uv run python -m flamingo_bot.ingest --dry-run --pretty
GCP_IMPERSONATE_SERVICE_ACCOUNT="$GCP_INGESTION_SERVICE_ACCOUNT" \
  uv run python -m flamingo_bot.ingest --publish --pretty
```

Dry-run performs no Azure or Firestore write. Its default mode reads the active
manifest and chunk hashes/vectors so new, reused, and removed counts are exact.
Use `--dry-run --local-only` only for offline parser/chunker work; that report is
explicitly labeled `local-source-only` and does not claim a production diff.
Publish embeds only new or changed content, reuses matching active-generation
embeddings, stages a complete new generation, validates count and representative
vector lookups across sources, and activates it transactionally.

Serving requires both the base `embedding` vector index and the compound
`source_id` plus `embedding` index in `READY` state. Retrieval takes at most
three hits per approved source, applies a `0.65` cosine-distance threshold, and
passes no more than seven diversified evidence chunks to Luna.

The staged generation and `ingestion_runs/{generation_id}` audit record persist
source revisions and coverage, parser/chunker/normalization versions, embedding
contract, discovered/accepted/skipped/failed/reused/embedded/removed counts,
start/completion times, and validation outcomes. A provider failure marks records
failed only after this process has successfully created its own staging records;
it cannot overwrite a colliding generation created by another publisher.

## Current source contract

- Flamingo Dossier: `data/dosje.csv` plus relations from `data/lidhje.csv`.
- Flamingo Revolution: `public/llms.txt`, public Markdown, selected structured
  TypeScript content, and public legal/editorial PDFs, including Flamingo Times.
  The public `/news/` index is also scanned for published article pages on each
  run; this content is served from the site's CMS, not its Git checkout.
- Diaspora Zbarkon: mission copy, primary content, the /pulsi page copy from
  `components/live-tracker-page.tsx`, and the protest participation series from
  `data/participation.ts`.
- Harta e Protestave: `data/locations.json` from `FLAMINGO_MAP_REPO`, with each
  city, protest, and the dataset-wide summary recorded separately. The map
  shares the Revolution retrieval ID but has its own `flamingo-map` revision.
- One-time snapshots: 17 dated Referendum 21/2024 pages and two topic records
  covering the author's Reddit Pulsi analysis under `content/snapshots/`.
  Routine ingestion never re-fetches these sites. Review any intentional
  snapshot update before publishing.

`data/participation.ts` uses the dedicated `participation_ts` parser. The generic
string extractor keeps only long string literals, which drops every figure and
date in that file and leaves the day notes with nothing to attach them to. The
dedicated parser reads the structure instead and emits four documents:
methodology, the day-by-day index, the day notes, and the timeline events. Every
chunk carries the estimate framing in both its sticky context and its
`legal_status`, because the values are a normalized model index and must never
be read as a count of people.
- Explicit duplicate exclusion: `diaspora_zbarkon/data/scandals.ts`.
- Explicit code-only exclusions: `src/data/referendum.ts`, `src/data/navIcons.ts`,
  and `src/data/socialIcons.ts` in the Revolution site.

Adding a new retrieval ID touches the catalog, `Settings`, retrieval's
`_SOURCE_IDS`, and sometimes a parser. The README's "Adding a new source"
section is the checklist; omitting a distinct ID from `_SOURCE_IDS` ingests and
bills the source without ever retrieving it.

The 2026-09-24 publication has 86 discovered files/pages, 595 documents, 1,549
chunks, no failed files, and no skips. These counts are a comparison baseline,
not a permanent requirement; source changes should change them visibly.

## Failure handling

- Any parser failure blocks publication.
- A missing or empty source set blocks publication.
- Azure or Firestore errors leave the active pointer unchanged.
- A staged generation is not evidence of successful publication.
- Do not retry a publish blindly; inspect the structured error and Firestore run
  state first.

## Data rollback

The rollback CLI accepts either the retained previous generation or one explicit,
validated generation ID:

```bash
uv run python -m flamingo_bot.rollback --previous --pretty
uv run python -m flamingo_bot.rollback --to GENERATION_ID --pretty
```

Rollback refuses an active, missing, unvalidated, non-superseded, or
chunk-count-mismatched target. It reruns a vector lookup, switches
`rag_meta/current` transactionally, marks the displaced generation superseded,
and writes `rollback_runs/{rollback_id}`. It never deletes either generation.
