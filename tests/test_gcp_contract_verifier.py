from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import verify_gcp_contract
from scripts.verify_gcp_contract import GcloudJsonRunner, VerificationError, verify

PROJECT = "example-project"
BILLING_ACCOUNT = "billingAccounts/000000-000000-000000"


class FakeRunner:
    def __init__(self, *, missing_database: bool = False, index_state: str = "READY") -> None:
        self.missing_database = missing_database
        self.index_state = index_state
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, arguments: Sequence[str]) -> Any:
        args = tuple(arguments)
        self.calls.append(args)
        if args[:3] == ("billing", "projects", "describe"):
            return {
                "billingAccountName": BILLING_ACCOUNT,
                "billingEnabled": True,
                "projectId": PROJECT,
            }
        if args[:3] == ("firestore", "databases", "describe"):
            if self.missing_database:
                raise VerificationError("not found")
            return {
                "name": f"projects/{PROJECT}/databases/flamingo-rag",
                "locationId": "europe-west3",
                "type": "FIRESTORE_NATIVE",
                "deleteProtectionState": "DELETE_PROTECTION_ENABLED",
            }
        if args[:4] == ("firestore", "indexes", "composite", "list"):
            return [
                {
                    "name": (
                        f"projects/{PROJECT}/databases/flamingo-rag/"
                        "collectionGroups/chunks/indexes/test-index"
                    ),
                    "queryScope": "COLLECTION",
                    "state": self.index_state,
                    "fields": [
                        {
                            "fieldPath": "embedding",
                            "vectorConfig": {"dimension": "1024", "flat": {}},
                        }
                    ],
                },
                {
                    "name": (
                        f"projects/{PROJECT}/databases/flamingo-rag/"
                        "collectionGroups/chunks/indexes/source-index"
                    ),
                    "queryScope": "COLLECTION",
                    "state": self.index_state,
                    "fields": [
                        {"fieldPath": "source_id", "order": "ASCENDING"},
                        {
                            "fieldPath": "embedding",
                            "vectorConfig": {"dimension": "1024", "flat": {}},
                        },
                    ],
                },
            ]
        if args[:3] == ("artifacts", "repositories", "describe"):
            return {
                "name": (
                    f"projects/{PROJECT}/locations/europe-west3/repositories/flamingo-containers"
                ),
                "format": "DOCKER",
            }
        if args[:3] == ("run", "services", "describe"):
            return {
                "metadata": {
                    "name": "flamingo-bot",
                    "labels": {"cloud.googleapis.com/location": "europe-west3"},
                },
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "autoscaling.knative.dev/minScale": "0",
                                "autoscaling.knative.dev/maxScale": "5",
                            }
                        },
                        "spec": {
                            "containers": [
                                {
                                    "env": [
                                        {"name": "FIRESTORE_DATABASE_ID", "value": "flamingo-rag"},
                                        {"name": "FLAMINGO_ENVIRONMENT", "value": "production"},
                                        {
                                            "name": "FLAMINGO_ALLOWED_ORIGINS",
                                            "value": "https://www.flamingorevolution.eu",
                                        },
                                        *[
                                            {
                                                "name": name,
                                                "valueFrom": {
                                                    "secretKeyRef": {
                                                        "name": f"secret-{name.lower()}",
                                                        "key": "latest",
                                                    }
                                                },
                                            }
                                            for name in (
                                                "AZURE_OPENAI_API_BASE",
                                                "AZURE_OPENAI_API_LLM_KEY",
                                                "FLAMINGO_SAFETY_SALT",
                                            )
                                        ],
                                    ]
                                }
                            ]
                        },
                    }
                },
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "traffic": [{"percent": 100, "revisionName": "flamingo-bot-00001"}],
                },
            }
        if args[:3] == ("run", "services", "get-iam-policy"):
            return {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}
        if args[:2] == ("secrets", "describe"):
            return {
                "name": f"projects/123/secrets/{args[2]}",
                "replication": {"userManaged": {"replicas": [{"location": "europe-west3"}]}},
            }
        if args[:3] == ("iam", "roles", "describe"):
            role_id = args[3]
            filename = (
                "ingestion-role.yaml" if role_id == "flamingoRagIngester" else "deployer-role.yaml"
            )
            role: Any = yaml.safe_load(Path(f"infra/iam/{filename}").read_text())
            return {
                "name": f"projects/{PROJECT}/roles/{role_id}",
                "includedPermissions": role["includedPermissions"],
            }
        raise AssertionError(f"Unexpected command: {args}")


def test_read_only_verifier_accepts_complete_matching_resources() -> None:
    runner = FakeRunner()

    findings = verify(
        project=PROJECT,
        billing_account=BILLING_ACCOUNT,
        contract_path=Path("infra/contract.yaml"),
        runner=runner,
    )

    assert findings
    assert all(item.passed for item in findings)
    assert all("list" not in call[:2] for call in runner.calls if call[0] == "run")


def test_verifier_reports_missing_resource_without_raw_error() -> None:
    findings = verify(
        project=PROJECT,
        billing_account=BILLING_ACCOUNT,
        contract_path=Path("infra/contract.yaml"),
        runner=FakeRunner(missing_database=True),
    )
    by_id = {item.id: item for item in findings}

    assert not by_id["firestore_database"].passed
    assert by_id["firestore_database"].detail == "resource metadata unavailable"


def test_verifier_rejects_a_different_billing_account() -> None:
    findings = verify(
        project=PROJECT,
        billing_account="billingAccounts/111111-111111-111111",
        contract_path=Path("infra/contract.yaml"),
        runner=FakeRunner(),
    )

    assert not next(item for item in findings if item.id == "billing_account_linked").passed


def test_verifier_requires_a_well_formed_private_billing_account_value() -> None:
    with pytest.raises(VerificationError, match="GCP_BILLING_ACCOUNT"):
        verify(
            project=PROJECT,
            billing_account="",
            contract_path=Path("infra/contract.yaml"),
            runner=FakeRunner(),
        )


def test_verifier_rejects_a_vector_index_that_is_still_building() -> None:
    findings = verify(
        project=PROJECT,
        billing_account=BILLING_ACCOUNT,
        contract_path=Path("infra/contract.yaml"),
        runner=FakeRunner(index_state="CREATING"),
    )

    assert not next(item for item in findings if item.id == "firestore_vector_index").passed


def test_gcloud_failure_never_reproduces_provider_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_run(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return verify_gcp_contract.subprocess.CompletedProcess(
            args=["gcloud"],
            returncode=1,
            stdout="",
            stderr="sensitive-provider-error-body",
        )

    monkeypatch.setattr(verify_gcp_contract.subprocess, "run", failed_run)

    with pytest.raises(VerificationError) as captured:
        GcloudJsonRunner()(["billing", "projects", "describe", PROJECT])

    assert "sensitive-provider-error-body" not in str(captured.value)
