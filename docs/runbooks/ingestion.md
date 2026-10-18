# Ingestion runbook

## Source update workflow

1. Update the four approved local repositories (Dossier, Revolution, Diaspora,
   and Harta e Protestave) with their normal Git workflow.
2. Inspect their changes and commit them. Ingestion never performs `git pull` and
   refuses a repository with tracked or untracked working-tree changes.
   Update optional research indexes in `FLAMINGO_RESEARCH_DIR` and collect
   [Lapsi references](#lapsi-references) locally before ingestion. These non-Git
   sources use content fingerprints checked again after parsing.
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
`source_id` plus `embedding` index in `READY` state. Retrieval gathers up to seven
candidates per source, applies a `0.65` cosine-distance threshold, and passes at
most seven diversified chunks to Luna. Translations of one protest day deduplicate;
distinct days remain eligible. Short generic escalation questions gain movement
and current-month search context, double the candidate pool, exclude matches more
than 0.18 behind the best distance, and use a bounded 0.08 recency bonus decaying
over 30 days. Named places, dates and day numbers retain their scope. The answer
model sees the original question; filing/publication dates never become incident
dates, and recorded cosine distances are unchanged.

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
- Diaspora Zbarkon: mission copy, primary content, and the protest participation
  series from `data/participation.ts`. Conditional labels from the /pulsi UI are
  excluded: a "no figures" fallback label is not an event or a measurement.
  Public protest safety guidance from
  `lib/safety-content.ts` cites `/siguria/` and uses the existing text extractor.
  Short contact-directory fields are not retained by that extractor; the live
  guide remains the reference for the full contact directory.
- Protest research: optional `research/day_*_sources.json` indexes under
  `FLAMINGO_RESEARCH_DIR`. Only public-report titles, publisher, summaries,
  publication/event-date distinctions, reporting lineage, and limitations enter
  evidence. Citations open the original article. Video/model inputs, raw local
  analysis, query logs, and snippet-only leads are excluded. Records with no
  recorded page-access status remain explicitly labeled as such in metadata.
- Harta e Protestave: `data/locations.json` from `FLAMINGO_MAP_REPO`, with each
  city, protest, and the dataset-wide summary recorded separately. The map
  shares the Revolution retrieval ID but has its own `flamingo-map` revision.
- Lapsi.al: optional `index.json` under `FLAMINGO_LAPSI_DIR`, containing only
  protest-related references and short publisher-provided introductions.
  See [collection, recovery and serving requirements](#lapsi-references).
- One-time snapshots: 17 dated Referendum 21/2024 pages and two topic records
  covering the author's Reddit Pulsi analysis under `content/snapshots/`.
  Routine ingestion never re-fetches these sites. Review any intentional
  snapshot update before publishing.

The `participation_days` parser emits methodology, an index, timeline events,
and individual bilingual day records from `data/participation.ts`. Each day
retains its number, event date, stable ID and source/livestream URLs. Chunks label
participation values as estimates, not headcounts. The legacy `participation_ts`
parser remains available; new formats are `participation_days/1` and
`protest_research/2`, preserving embedding reuse for unaffected sources. Research
filing dates stay separate from explicitly recorded incident dates.

- Explicit duplicate exclusion: `diaspora_zbarkon/data/scandals.ts`.
- Explicit code-only exclusions: `src/data/referendum.ts`, `src/data/navIcons.ts`,
  and `src/data/socialIcons.ts` in the Revolution site.

Adding a new retrieval ID touches the catalog, `Settings`, retrieval's
`_SOURCE_IDS`, and sometimes a parser. The README's "Adding a new source"
section is the checklist; omitting a distinct ID from `_SOURCE_IDS` ingests and
bills the source without ever retrieving it.

Use the dry-run report and `ingestion_runs` audit records to review generation
counts and validation results; run-specific progress notes stay local.

## Lapsi references

The local collector selects protest-related references from 31 May 2026, not a
rolling four-month window. Later runs check from the last successful checkpoint
with a 48-hour overlap. Monthly sitemap metadata is also checked across the
historical range to detect newly indexed older articles and edits. Selection
uses URL markers, then publisher headlines/intros/sections; topic scores are not
popularity or factual-verification scores. Discovery is bounded, not exhaustive.

Set `FLAMINGO_LAPSI_DIR=.local/lapsi` in the private `.env`, then preview and save:

```bash
uv run python scripts/snapshot_lapsi.py --dry-run --pretty
uv run python scripts/snapshot_lapsi.py --write --pretty
```

Only protest-related coverage, student protests, police response, diaspora
mobilisation and protest-linked coastal issues are eligible. Each reference
retains its headline, publisher introduction (at most 280 characters), dates,
byline, section, canonical URL, aliases and selection topics. No article bodies,
AI-generated abstracts or private analysis are retained. Publication dates are
not incident dates. Collection makes no model calls or cloud writes and installs
no recurring automation.

- `--url https://lapsi.al/ARTICLE` checks a hand-picked URL; repeat for several.
  Topic/date checks still apply, and manual collection never advances the global
  checkpoint. Use it for relevant articles missed by URL-based discovery.
- `--output /path/to/private/lapsi` changes the local output; point
  `FLAMINGO_LAPSI_DIR` there too and keep the directory outside Git.
- `--full-rescan` rechecks the historical range. Topic-filter version changes
  also trigger a rescan. Defaults cap fetches at 2,000 articles and 60 monthly
  sitemaps; adjust `--max-articles` or `--max-sitemaps` for an incomplete scan.

`index.json` accumulates references rather than replacing history with each
increment. Existing metadata is rechecked against topic rules; `removed_unrelated`
reports rejected entries. Only this file is ingested and fingerprinted.
`checkpoint.json`, the writer lock and `collection-cache.json` remain local.
The writer lock prevents concurrent collectors from updating one directory.
Unchanged cached metadata may be reused for 24 hours after an interrupted run;
the live sitemap and crawl policy are still checked. The checkpoint advances
only after a complete scan and atomic index save. Network, policy, parsing and
resource-limit failures leave the index and checkpoint unchanged; a crash
between saves safely replays.
Dry runs never update local files. Review counts, date range and preview before
publishing; configured empty sources block publication.

The collector honors `robots.txt`, content-use restrictions and published delays
(at least 0.5 seconds between requests), without bypassing access controls.
Lapsi's [observed policy](https://lapsi.al/robots.txt) on 18 October 2026 was
`search=yes,ai-train=no,use=reference`. See the
[content-use definitions](https://developers.cloudflare.com/bots/additional-configurations/managed-robots-txt/#content-use-signal).
This is a recorded publisher preference, not a blanket copyright licence.
The prompt permits article references and brief supplied excerpts, not full-text
summaries or invented details; summaries require a separately confirmed reuse
basis and an intentional implementation change. Detailed research on the same
article is preferred, not counted as independent corroboration of the reference.

After reviewing the snapshot, use the normal ingestion dry-run/publish commands
above. The backend must be released once with the new `lapsi` retrieval ID before
the deployed bot can search this source. Later data-only updates need no app
deployment. No new cloud resource, index, model deployment or workflow is needed.

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
