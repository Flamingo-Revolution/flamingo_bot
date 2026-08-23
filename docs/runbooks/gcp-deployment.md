# GCP deployment runbook

## Status

The dedicated project, Frankfurt Firestore data plane, least-privilege
identities, first publication, and production Cloud Run service are live and
verified. Production releases run through the protected GitHub environment and
keyless WIF workflow; each remains owner-approved.

## 1. Owner authorization and private billing link

The approved target uses:

- Project ID: supplied privately as `GCP_PROJECT_ID`
- Billing account: supplied privately as `GCP_BILLING_ACCOUNT`
- Region: `europe-west3`

The owner must confirm the intended account and any applicable credit balance in
Cloud Billing before deployment. The verifier checks that billing is enabled and
that the project is linked to the account provided at runtime; neither identifier
is stored in the public contract. Azure usage remains outside GCP billing.

## 2. Read-only preflight

With the intended account selected, verify rather than infer:

```bash
gcloud config get-value account
gcloud config get-value project
gcloud billing projects describe "$PROJECT_ID"
gcloud firestore databases list --project "$PROJECT_ID"
gcloud run services list --project "$PROJECT_ID" --region europe-west3
gcloud artifacts repositories list --project "$PROJECT_ID" --location europe-west3
```

The first two commands must identify the intended operator and project. The
billing project response can confirm the linked billing account, but the credit
name, balance, expiry, and SKU eligibility still require the Billing console or
program documentation.

A pre-existing project was evaluated and rejected as the deployment target.
That private inventory is retained only in the ignored security audit; it does
not help operators deploy this project.

The verified production state on 2026-08-23 is:

- The dedicated project is linked to the approved billing account.
- Firestore Native `flamingo-rag` is in `europe-west3` with delete protection.
- The base and `source_id`-filtered 1,024-dimensional indexes are `READY`.
- Generation `20260823T094238Z-002c3ce5269a` is active with 884 chunks.
- The runtime identity is read-only; the ingestion identity has deletion-free,
  database-conditioned create/read/list/update access.
- Artifact Registry, three Frankfurt-replicated secrets, the WIF-bound deployer,
  and the public Cloud Run service match the repository contract.
- Cloud Run has minimum scale zero, a service maximum of five instances, and
  one validated revision receiving 100% traffic with its predecessor retained.
- A project-filtered monthly budget reports gross usage before credits at 50%
  actual, 90% forecast, and 100% actual thresholds.

The repository also provides a safe, exact-resource verifier. Before
provisioning it should fail because the resources are absent. After the complete
publish/deploy workflow, every finding must pass:

```bash
uv run --env-file .env python scripts/verify_gcp_contract.py \
  --pretty
```

The verifier runs only read-only `describe`, index-list, and IAM-policy calls for
the resources named by `infra/contract.yaml`. It emits pass/fail findings and
does not print provider payloads, environment values, secret metadata bodies, or
credential material.

## 3. Provisioning

Enable only the required APIs and create the fixed resources described in
[`../../infra/README.md`](../../infra/README.md).
The database location is immutable, so `europe-west3` must be rechecked before
creation. Enable delete protection at creation time.

The Firestore database, vector indexes, production delivery resources, and
least-privilege bindings are complete. A fresh environment must create the same
regional or region-constrained resources:

- Docker Artifact Registry `flamingo-containers` in `europe-west3`.
- GitHub deployment service account; reuse the existing dedicated runtime
  identity for Cloud Run rather than broadening it.
- Named secrets `azure-openai-api-base`, `azure-openai-api-llm-key`, and
  `flamingo-safety-salt`; grant the runtime identity access at secret scope.
- GitHub Workload Identity Pool/provider restricted to the owner/repository and
  protected production environment.
- Repository-defined deletion-free Cloud Run deploy role, repository-scoped
  Artifact Registry writer binding, and runtime-account-scoped service-account
  user binding as documented in `infra/README.md`.
- Billing budget alerts at conservative thresholds.
- A second Firestore database, `flamingo-quotas`, holding only request counters,
  with a conditional write grant and a TTL policy. See the next section.

Never place a GCP JSON key in GitHub. Never grant the runtime identity ingestion
or provisioning permissions.

## 3a. Request quotas

### Why the counters have their own database

Firestore grants write access per database. There is no way to grant write on one
collection, so counters kept beside the corpus would force the serving identity to
hold write access over `rag_generations` and `rag_meta/current` as well, and a
compromised container could then rewrite published evidence or repoint the active
generation. The counters therefore live in a separate database and the runtime
identity's grant is conditioned on it. Its access to the corpus stays read-only,
which is the reason ingestion runs under a different principal in the first place.

Provision it once:

```bash
gcloud firestore databases create \
  --database=flamingo-quotas \
  --location="$REGION" \
  --type=firestore-native \
  --project "$GCP_PROJECT_ID"

gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:$GCP_RUNTIME_SERVICE_ACCOUNT" \
  --role=roles/datastore.user \
  --condition='title=quota-db-only,expression=resource.name.endsWith("/databases/flamingo-quotas")'

gcloud firestore fields ttls update expires_at \
  --collection-group=request_quotas --enable-ttl \
  --project "$GCP_PROJECT_ID" --database=flamingo-quotas
```

Keep `roles/datastore.viewer` on the corpus database. Do not replace it with an
unconditional `roles/datastore.user`; that is the grant this separation exists to
avoid. The TTL policy is housekeeping only, since nothing reads a closed window.

Confirm the runtime identity ends up with exactly a read grant on the corpus and a
conditioned write grant on the counters:

```bash
gcloud projects get-iam-policy "$GCP_PROJECT_ID" \
  --flatten='bindings[].members' \
  --filter="bindings.members:$GCP_RUNTIME_SERVICE_ACCOUNT" \
  --format='table(bindings.role, bindings.condition.title)'
```

### Limits

Two durable ceilings bound what the public endpoint can spend, both counted in
Firestore so every Cloud Run instance shares one number:

| Control | Default | Scope | Storage |
| --- | --- | --- | --- |
| Burst limiter | 20 per minute | One caller, one instance | In process |
| Visitor quota | 10 per hour | One caller, whole service | `flamingo-quotas` |
| Daily budget | 100 per day | Whole service | `flamingo-quotas` |

Change any of them through the repository variables named in the deploy workflow;
no code release is needed. Points worth knowing before tuning them:

- Windows are fixed and aligned to the UTC hour and UTC midnight, so a visitor can
  spend the end of one window and the start of the next in quick succession. The
  burst limiter covers that spike and the daily budget still bounds the day.
- A quota is charged before retrieval and before any model call, so a refused
  request costs nothing. A request refused by the daily budget does not spend the
  visitor's hourly allowance, and vice versa.
- The service fails closed. If the quota counters cannot be read, `/v1/chat`
  answers `503 quota_unavailable` rather than serving an unmetered request.
  Retrieval depends on the same database, so this costs no availability the
  request would otherwise have had.
- Visitors are identified by the address Cloud Run appends to `X-Forwarded-For`,
  selected by `FLAMINGO_TRUSTED_PROXY_HOPS`. That default of `0` is correct only
  while traffic reaches Cloud Run directly. **Putting an external HTTPS load
  balancer or Cloud Armor in front changes the header shape; set the variable to
  `1` in the same change, or every visitor collapses into one bucket.**

## 4. First ingestion and local verification

Run the local dry-run first:

```bash
uv run python -m flamingo_bot.ingest --dry-run --pretty
```

Publish through the dedicated ingestion identity without replacing the
operator's existing ADC file. Set `GCP_INGESTION_SERVICE_ACCOUNT` in the private
operator shell before running the command:

```bash
GCP_IMPERSONATE_SERVICE_ACCOUNT="$GCP_INGESTION_SERVICE_ACCOUNT" \
  uv run --env-file .env python -m flamingo_bot.ingest --publish --pretty
```

Publication writes a new immutable generation, validates it, and transactionally
changes `rag_meta/current`. It does not delete the previous generation. After a
successful publish, run the local API against the same database and verify
`/status`, cited answers, refusals, and source links.

Serving requires both indexes in `READY` state. Retrieval takes at most three
hits per approved source, applies the `0.65` cosine-distance threshold, and sends
no more than seven diversified evidence chunks to Luna.

## 5. GitHub and deployment

After the owner creates the repository and makes the initial commit, configure
these GitHub Actions repository variables:

- `GCP_PROJECT_ID`
- `GCP_WIF_PROVIDER`
- `GCP_DEPLOY_SERVICE_ACCOUNT`
- `GCP_RUNTIME_SERVICE_ACCOUNT`
- `FLAMINGO_ALLOWED_ORIGINS`

Protect the `production` GitHub environment with required review. The manual
`Deploy to Cloud Run` workflow runs all tests, authenticates with WIF, and pushes
an immutable SHA-tagged image. When a service already exists, it deploys a tagged
Frankfurt candidate with zero traffic. Cloud Run does not support `--no-traffic`
while creating a service, so the workflow detects the first deployment and uses
that revision as the bootstrap candidate. It checks `/health` and `/status`,
then runs the versioned
`evals/smoke.yaml` question through that candidate's URL and requires a cited
Flamingo Revolution answer. The runner does not print the question or answer.
Only a passing candidate receives 100% traffic. A failed normal candidate loses
its tag without displacing the current revision. If the first bootstrap candidate
fails, the workflow also removes public invocation so the failed service is not
left exposed.

Production `/v1/chat` requests must carry an allowed host-site `Origin`; health,
readiness, and widget assets remain readable without one. This reduces casual
direct-endpoint abuse but is not authentication because non-browser clients can
forge headers. The instance ceiling, per-client limiter, budget alerts, and Azure
usage monitoring remain necessary cost controls.

## 6. Rollback

Data rollback and application rollback are independent:

- Application: move Cloud Run traffic to the last known-good revision.
- Data: use `uv run python -m flamingo_bot.rollback --previous --pretty`, or
  name a reviewed retained generation with `--to GENERATION_ID`. The command
  revalidates the manifest, chunk count, and vector lookup before changing the
  pointer transactionally and recording `rollback_runs/{rollback_id}`.

Do not delete a failed generation as part of incident response. Preserve it for
diagnosis, then use a separately reviewed, generation-ID-bounded cleanup task.

## References

- [Firestore database management](https://cloud.google.com/firestore/docs/manage-databases)
- [Firestore vector search](https://cloud.google.com/firestore/native/docs/vector-search)
- [Cloud Run deployment](https://cloud.google.com/run/docs/deploying)
- [Workload Identity Federation for deployments](https://github.com/google-github-actions/auth)
