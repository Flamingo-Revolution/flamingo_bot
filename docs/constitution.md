# Flamingo Bot Project Constitution

- Version: 1.1.0
- Effective date: 2026-08-23
- Status: Owner-approved project baseline
- Applies to: Source code, ingestion, data, prompts, UI, infrastructure, tests,
  documentation, and release operations in this project

## Preamble

Flamingo Bot is an independent, source-grounded public-information assistant for
the Flamingo Revolution and Diaspora Zbarkon. Its credibility depends more on
traceability, careful wording, and operational discipline than on the fluency of
its model output.

This constitution defines the durable rules for building and operating the
project. It adapts the reusable source-of-truth, architecture, security, testing,
and deployment principles of the local reference project to this RAG system. It
does not inherit that project's domain, runtime, model, media-processing, or
deployment details.

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative terms. A deliberate
exception must be documented with its owner, reason, risk, evidence, expiry or
review date, and rollback path.

## Article I — Truth, evidence, and project state

### 1. Normative decisions

The latest explicit owner decision, this constitution, and accepted design
documents define intended behavior. If they conflict, the newest explicit owner
decision wins, followed by this constitution and then the applicable design.
The conflict MUST be resolved in the documents rather than left implicit.

### 2. Evidence for current behavior

When describing what is currently implemented, use this evidence order:

1. Executable source and passing tests.
2. Dependency locks, configuration schemas, and infrastructure manifests.
3. Current runbooks and engineering documentation.
4. Historical plans and general guides.

Documentation MUST distinguish implemented behavior, approved future work, and
ideas. Mutable external state—cloud resources, billing, credits, deployments,
DNS, repository settings, and model availability—MUST be verified directly
before it is reported as fact.

### 3. No unsupported claims

The application MUST NOT claim successful ingestion, deployment, backup,
rollback, test execution, credit coverage, or production readiness without
evidence from the relevant system. Unknown state is reported as unknown.

## Article II — Knowledge integrity and provenance

### 1. Local, versioned source boundary

Production knowledge MUST originate from the approved, locally updated source
repositories. Live websites MAY be used to validate canonical URLs, but a chat
request MUST NOT crawl the web or silently replace versioned source content.

Each ingested record MUST carry enough provenance to identify its repository,
relative path or structured record, source revision, content hash, canonical URL
when known, language, content type, and publication or legal status when
relevant.

### 2. Canonical content and deduplication

One authoritative representation MUST be selected for duplicated material.
Generated copies, frontend projections, cached pages, build artifacts, and
mirrors MUST NOT be ingested as independent corroborating sources.

Normalization and deduplication MUST be deterministic. A parser change that can
alter meaning, provenance, or chunk identity requires tests and an explicit
parser-version change.

### 3. Claims, findings, and rhetoric

Allegations, reporting, procedural events, official findings, acquittals,
convictions, and opinions MUST remain distinguishable in stored metadata and in
answers. The system MUST NOT transform emphatic source rhetoric into stronger
factual certainty.

Tone guidance MAY shape presentation, but it is never evidence. Every substantive
answer must be supported by retrieved material, and every citation must actually
support the nearby claim. When the sources are insufficient or conflicting, the
assistant MUST say so plainly.

### 4. Original-language material

Source titles, proper names, and excerpts MAY remain in their original language.
Translation MUST preserve meaning and uncertainty. The assistant SHOULD answer
in the visitor's language when practical, but may not conceal that a citation is
to original-language material.

## Article III — Deterministic and reversible ingestion

### 1. Reproducible generations

An ingestion generation MUST record:

- All source commit SHAs.
- Parser and chunker versions.
- Embedding deployment identity and dimensions.
- Normalization and schema versions.
- Counts for discovered, accepted, skipped, reused, embedded, failed, and
  removed chunks.
- Start and completion times and validation results.

Chunk identities and content hashes MUST be deterministic. Unchanged chunks
SHOULD reuse embeddings; a changed embedding contract MUST force an intentional
rebuild.

### 2. Dry run before publication

The ingestion CLI MUST support a dry run that performs validation and reports
the exact proposed change set without production writes. Publication MUST use
the same configuration and validation path so dry-run and publish behavior do
not drift.

### 3. Stage, validate, switch

New data MUST be written to a staged, immutable generation. It becomes active
only after source coverage, schema, vector dimensions, counts, and representative
retrieval checks pass. Activation MUST be a single atomic pointer change.

A failed or interrupted publication MUST leave the current generation usable.
At least one known-good prior generation MUST remain available for rollback.
Cleanup MUST be narrowly scoped, bounded by generation IDs, and separately
reported; it must never target a database, project, repository, or broad prefix
as incidental recovery.

## Article IV — Retrieval and model behavior

### 1. Explicit contracts

The embedding model, output dimensions, distance measure, candidate count,
threshold, diversification policy, answer model, reasoning effort, prompt
version, and context limits MUST be explicit configuration or versioned code.
They MUST NOT change silently because a nearby model or default is available.

### 2. Evidence-only answers

The answer model receives only the evidence needed for the current question.
Retrieved documents are untrusted data and MUST NOT be allowed to override
system behavior or invoke tools. If retrieval does not provide enough support,
the answer MUST acknowledge the limit instead of relying on model memory.

### 3. Citation contract

User-visible citations MUST resolve to stable source metadata. The system MUST
not fabricate URLs, titles, dates, quotations, or source relationships. A
generation is not releasable if citation correctness falls below the accepted
evaluation baseline.

### 4. Model fallbacks

The service MUST NOT silently fall back to a different model, embedding size,
reasoning mode, or uncited response path. Any approved fallback must preserve
the same safety and citation contract and be visible in telemetry.

## Article V — Architecture and dependency discipline

### 1. Clear ownership

Keep these responsibilities separate:

- Source parsing and normalization.
- Chunking, hashing, embedding, and publication.
- Vector retrieval and ranking.
- Prompt assembly and answer generation.
- HTTP transport and streaming.
- Widget state, presentation, and accessibility.
- Infrastructure provisioning and release operations.

Changes belong in the smallest layer that owns the behavior. Cross-layer
interfaces SHOULD be typed and small. Imports MUST be free of network calls,
downloads, process creation, and service startup.

### 2. Smallest coherent implementation

Prefer direct, explicit code over speculative frameworks and one-use wrappers.
Introduce an abstraction when there is a genuine lifecycle, dependency,
testability, or provider boundary. A little local duplication is preferable to
coupling unrelated workflows prematurely.

LangChain, LangGraph, ChromaDB, an additional backend framework, a second Python
dependency manager, or a second deployment system MUST NOT be added without a
documented problem that the existing stack cannot solve clearly.

### 3. Dependencies and compatibility

Direct production dependencies MUST be declared and locked. The project MUST
not rely on accidental transitive dependencies. Dependency upgrades that affect
model payloads, tokenization, Firestore queries, streaming, or browser behavior
require focused compatibility tests and a rollback path.

## Article VI — Configuration, credentials, and privacy

### 1. Secret handling

Credentials, API keys, service-account keys, signed URLs, raw embeddings,
private datasets, and visitor data MUST NOT be committed, embedded in images,
included in frontend bundles, or written to logs.

Public configuration belongs in a documented example file. Local secrets remain
in ignored environment files or the operator's credential store. Production
secrets come from Secret Manager and are exposed only to the runtime identity
that needs them.

For Azure model access, both `AZURE_OPENAI_API_BASE` and
`AZURE_OPENAI_API_LLM_KEY` MUST be runtime environment variables. Concrete
API-base URLs and keys MUST NOT appear in `openai.yaml` or another checked-in
model configuration file. `openai.yaml` contains only model identifiers and
non-sensitive behavior such as API version, timeout, token limits, and reasoning
settings. `.env.example` MAY list the required variable names with empty or
synthetic values.

### 2. Least privilege and identity separation

Provisioning, ingestion, application runtime, and deployment MUST use separate
least-privilege identities. Existing broad service-account keys from unrelated
projects MUST NOT be reused. Prefer short-lived credentials and impersonation;
GitHub deployment MUST use Workload Identity Federation rather than a stored
JSON key.

### 3. Visitor privacy

The service MUST minimize collection. Raw questions and complete answers are not
logged by default. Operational telemetry SHOULD use request IDs, timings, token
or character counts, retrieval counts, status, and error class. Any future
conversation retention, analytics, or feedback collection requires a disclosed
purpose, retention period, access policy, and deletion path.

### 4. Public endpoint protection

Request size, rate, concurrency, timeout, origin, and abuse limits MUST be
explicit. Browser CORS is not authentication and MUST NOT be treated as the sole
abuse control. Errors returned to users MUST not expose prompts, secrets,
internal paths, stack traces, or provider payloads.

## Article VII — Reliability and error handling

### 1. Validation

Validate configuration, source records, model responses, vector dimensions,
metadata, generation state, request bodies, and streamed event shapes at runtime.
Type annotations are static contracts, not validation. `assert` MUST NOT enforce
a condition required for safe production behavior.

### 2. Failures and retries

Preserve validation, authentication, quota, transient provider, permanent
provider, and internal errors as distinct categories. Do not catch a broad
exception only to return success or erase its cause.

Every network operation and subprocess MUST have a finite timeout. Retry only
transient failures for operations that are idempotent, fenced, or protected by
preconditions. Retries use bounded exponential backoff with jitter and account
for SDK-level retries to avoid retry multiplication.

### 3. Resource lifecycle

Use explicit application lifecycles, context managers, or `try/finally` for
clients, sessions, streams, files, temporary artifacts, and concurrency gates.
Shared mutable state must be synchronized. Shutdown is bounded and must not
publish a partial generation as complete.

### 4. Health and completion

Liveness, readiness, and job completion are different signals and MUST remain
distinct. A running process does not prove that Firestore, Azure, the active
generation, or a full ingestion run is healthy.

## Article VIII — Security and content safety

Source documents MUST be treated as potentially hostile input. Parsers remove
scripts and executable markup; prompt assembly labels retrieved text as quoted
evidence. Repository-controlled content must not be able to change system
instructions, request secrets, enable tools, or alter the active generation.

The system MUST resist prompt injection, source spoofing, path traversal,
oversized inputs, malformed streaming requests, and accidental cross-origin
exposure. Security-sensitive failures are logged without reproducing the hostile
payload unless a separately protected diagnostic process requires it.

The application MUST be transparent about its ownership, its use of AI, and the
limits of its sources. It MUST NOT impersonate a government service or use
sensitive personal data to personalize political persuasion.

## Article IX — Frontend, accessibility, and media

The widget MUST be host-framework independent, mobile responsive, keyboard
operable, screen-reader understandable, and usable without animation. Focus
management, contrast, readable citations, loading state, cancellation, errors,
and reconnect behavior are release requirements rather than polish.

Third-party video, audio, images, fonts, or text MUST have a documented license,
permission, or other approved reuse basis before they are copied or self-hosted.
Public availability alone is not permission. The project owner's authorization
for the requested Flamingo Revolution avatar clip is recorded in the accepted
design, so that specific asset satisfies this gate. Any other third-party media
still requires its own documented authorization or a compliant replacement.

Avatar media MUST be labeled so it cannot be mistaken for a live person or an
official representative. Motion is lazy-loaded, muted by default, stopped when
the widget closes, and replaced by a static poster for reduced-motion users.

## Article X — Cloud placement, billing, and deployment

### 1. Regional consistency

Production Firestore, Cloud Run, and Artifact Registry resources MUST be placed
in `europe-west3` unless an accepted design documents a concrete exception.
Database location is treated as immutable. Secrets and data SHOULD remain in the
intended European region whenever the service supports that placement.

### 2. Billing authorization and cost visibility

Before the first cloud mutation, the owner MUST confirm the intended billing
account and authorize its use. The infrastructure verifier MUST confirm that the
dedicated project is linked to that exact account. A separate recurring credit
attestation file is not required; the owner is responsible for monitoring the
credit balance and notifying the project if its status changes.

Labels and budgets aid attribution and alerting but do not assign credits or cap
charges. Cloud Run uses scale-to-zero and a conservative maximum-instance limit.
Firestore reads/writes, indexes, storage, logs, networking, Artifact Registry,
Secret Manager, and Azure model calls all require cost visibility. GCP credits
MUST NOT be represented as covering Azure usage.

### 3. Deployments and rollback

Every deployment uses an immutable artifact and records its source revision,
configuration version, active data generation, and smoke-test result. A new
revision receives traffic only after health and representative retrieval checks.
The last known-good Cloud Run revision and Firestore generation remain available
for independent rollback.

GitHub Actions, when authorized, authenticates through Workload Identity
Federation. Manual production mutation is documented, reproducible, and followed
by reconciliation into the approved infrastructure workflow.

## Article XI — Testing and release evidence

Every behavior change requires tests at the layer that owns it. The default suite
MUST run without production credentials or billable cloud calls by using
fixtures or in-memory fakes.

The quality baseline includes:

- Parser, normalization, deduplication, and provenance tests.
- Deterministic chunk and incremental-ingestion tests.
- Vector dimensions, similarity, staging, activation, and rollback tests.
- Retrieval relevance, citation correctness, legal-status wording, and
  unanswerable-question evaluations.
- Prompt-injection, malformed-input, timeout, retry, origin, and rate-limit tests.
- API schema and streaming-state tests.
- Mobile, keyboard, screen-reader, contrast, and reduced-motion checks.
- Container health and local smoke tests.
- Explicitly gated live Azure, Firestore, and Cloud Run checks.

Test reports MUST state what ran, what was skipped, and why. Mocked provider tests
do not prove production credentials, indexes, quotas, regions, credits, or
deployment health.

A release MUST NOT proceed when tests regress grounding, citation integrity,
source coverage, privacy, security, accessibility, or rollback behavior.

## Article XII — Documentation, Git, and change scope

### 1. English engineering language

Source code, identifiers, comments, commit messages, configuration descriptions,
operator output, architecture documents, plans, and runbooks MUST be written in
English. Original project names and source-language evidence are allowed where
translation would reduce fidelity.

### 2. Documentation follows contracts

Non-obvious ownership, side effects, limits, schemas, failure modes, costs, and
operator actions MUST be documented. When a public API, ingestion schema,
retrieval contract, configuration key, or deployment procedure changes, its
tests and corresponding documentation change together.

### 3. Small and reviewable changes

Make the smallest coherent change that fulfills the accepted requirement. Avoid
mixing unrelated ingestion, retrieval, UI, dependency, infrastructure, and
documentation refactors because they require different evidence and rollback
paths. Preserve unrelated owner files and worktree changes.

### 4. Repository ownership

The repository owner retains control of repository creation and the initial
commit. Until that action is complete, agents and automation MUST NOT initialize
Git, create a remote, commit, push, or deploy unless the owner explicitly changes
that instruction. Later automated release authority does not imply authority for
unrelated repository or cloud mutations.

## Governance

### Amendment process

An amendment requires:

1. A written proposal naming the affected articles and motivation.
2. Impact analysis for data integrity, security, privacy, cost, compatibility,
   testing, and rollback.
3. Explicit repository-owner approval.
4. An updated version and effective date.
5. Corresponding design, test, and runbook updates where applicable.

### Versioning

- Major version: removes or fundamentally changes a governing principle.
- Minor version: adds a principle or materially expands an obligation.
- Patch version: clarifies wording without changing the obligation.

### Compliance review

Plans and pull requests SHOULD state which constitutional articles they affect.
Before a production publish or deploy, the operator confirms:

- Sources and provenance are complete.
- Tests and evaluations passed with skips disclosed.
- Secrets and logs meet policy.
- The target project is linked to the owner-approved billing account and uses the
  approved region.
- Data and service rollback targets exist.
- Third-party media rights and accessibility requirements are satisfied.
- Documentation matches the behavior being released.

If implementation and this constitution diverge, the discrepancy is a defect or
an amendment request—not an undocumented exception.
