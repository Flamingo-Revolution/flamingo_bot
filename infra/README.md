# GCP infrastructure contract

[`contract.yaml`](contract.yaml) is the machine-readable infrastructure
contract, guarded by the default test suite. This directory records intended
infrastructure; it does not prove that any resource exists. The owner authorized
the staged mutations documented in
[`../docs/runbooks/gcp-deployment.md`](../docs/runbooks/gcp-deployment.md).

## Fixed resource choices

| Resource | Value |
| --- | --- |
| GCP project | Dedicated project supplied as `$PROJECT_ID` |
| Billing account | Owner-approved account supplied privately |
| Firestore mode | Native |
| Firestore database | `flamingo-rag` |
| Firestore location | `europe-west3` |
| Delete protection | Enabled |
| Chunk collection group | `chunks` |
| Vector fields | `embedding`; compound `source_id` + `embedding` |
| Vector dimensions | 1,024 |
| Vector distance | Cosine at query time |
| Cloud Run service | `flamingo-bot`, `europe-west3`, min 0, max 5 |
| Artifact Registry | `flamingo-containers`, Docker, `europe-west3` |

Set `PROJECT_ID` from the ignored local environment, then create the database
and vector indexes with the following project-scoped commands.

```bash
gcloud firestore databases create \
  --project "$PROJECT_ID" \
  --database flamingo-rag \
  --location europe-west3 \
  --type firestore-native \
  --delete-protection

gcloud firestore indexes composite create \
  --project "$PROJECT_ID" \
  --database flamingo-rag \
  --collection-group chunks \
  --query-scope collection \
  --field-config infra/firestore/vector-index.json

gcloud firestore indexes composite create \
  --project "$PROJECT_ID" \
  --database flamingo-rag \
  --collection-group chunks \
  --query-scope collection \
  --field-config infra/firestore/vector-index-by-source.json
```

Both indexes use flat 1,024-dimensional vectors. The compound index supports
small source-filtered lookups before final diversification. Cosine is selected
by each Firestore query rather than stored in the index definitions.

## Identity boundary

- Runtime identity: conditionally bound `roles/datastore.viewer` for only the
  `flamingo-rag` database; access only to its three named Secret Manager secrets
  at secret scope.
- Ingestion identity: the repository-defined `flamingoRagIngester` custom role,
  conditionally bound to only `flamingo-rag`. It has database transaction plus
  document read/query/create/update permissions, but no delete permission.
- GitHub deploy identity: the repository-defined
  `flamingoCloudRunDeployer` custom role, Artifact Registry writer on only
  `flamingo-containers`, and service-account user on only the runtime identity.
  Authentication is through WIF. The custom role can set the service's public
  invoker policy but cannot delete services or revisions.
- Provisioner: temporary bootstrap permissions only; not used by the application.

Do not copy or reuse credentials from an unrelated project. Local ingestion
should use Application Default Credentials with dedicated service-account
impersonation.

Firestore IAM is granted at project level, so both application identities need
the documented database resource condition. Do not grant either Firestore role
without this condition in a project that also contains unrelated databases:

```bash
gcloud iam roles create flamingoRagIngester \
  --project "$PROJECT_ID" \
  --file infra/iam/ingestion-role.yaml

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$RUNTIME_SERVICE_ACCOUNT" \
  --role roles/datastore.viewer \
  --condition "expression=resource.name==\"projects/$PROJECT_ID/databases/flamingo-rag\",title=flamingo_runtime_database,description=Read only the Flamingo RAG database"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$INGESTION_SERVICE_ACCOUNT" \
  --role "projects/$PROJECT_ID/roles/flamingoRagIngester" \
  --condition "expression=resource.name==\"projects/$PROJECT_ID/databases/flamingo-rag\",title=flamingo_ingestion_database,description=Publish only the Flamingo RAG database"
```

The custom ingestion role intentionally omits `datastore.entities.delete`.
Generation cleanup requires a separate, time-bounded identity and a reviewed
generation-ID-bounded tool; it must not broaden the regular publisher.

Create the narrowly permissioned Cloud Run role during the later production
delivery stage. Bind its
Artifact Registry and service-account permissions at those individual resources,
not at project scope:

```bash
gcloud iam roles create flamingoCloudRunDeployer \
  --project "$PROJECT_ID" \
  --file infra/iam/deployer-role.yaml

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$GITHUB_DEPLOY_SERVICE_ACCOUNT" \
  --role "projects/$PROJECT_ID/roles/flamingoCloudRunDeployer"

gcloud artifacts repositories add-iam-policy-binding flamingo-containers \
  --project "$PROJECT_ID" \
  --location europe-west3 \
  --member "serviceAccount:$GITHUB_DEPLOY_SERVICE_ACCOUNT" \
  --role roles/artifactregistry.writer

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SERVICE_ACCOUNT" \
  --project "$PROJECT_ID" \
  --member "serviceAccount:$GITHUB_DEPLOY_SERVICE_ACCOUNT" \
  --role roles/iam.serviceAccountUser
```

The Cloud Run custom role is project-bound because it must be able to create the
service before a service-level policy exists. Its WIF principal must therefore
be restricted to the exact repository and protected `production` environment.
The role intentionally omits `run.services.delete`, `run.revisions.delete`, job,
worker-pool, and SSH permissions.

References:

- [Configure access conditions for one Firestore database](https://docs.cloud.google.com/firestore/native/docs/manage-databases#configure_database_access_conditions)
- [Firestore operation-to-permission mapping](https://docs.cloud.google.com/firestore/native/docs/security/iam)
