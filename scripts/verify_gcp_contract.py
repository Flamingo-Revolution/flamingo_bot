"""Verify deployed GCP resources against the repository contract without writes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VerificationError(RuntimeError):
    """A read-only provider check could not return usable metadata."""


class JsonRunner(Protocol):
    def __call__(self, arguments: Sequence[str]) -> Any: ...


@dataclass(frozen=True)
class Finding:
    id: str
    passed: bool
    detail: str


class GcloudJsonRunner:
    """Run an exact read-only gcloud command and discard provider error bodies."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def __call__(self, arguments: Sequence[str]) -> Any:
        environment = dict(os.environ)
        environment["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
        try:
            completed = subprocess.run(
                ["gcloud", *arguments, "--format=json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VerificationError("gcloud metadata request could not run") from exc
        if completed.returncode != 0:
            raise VerificationError("gcloud metadata request failed")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise VerificationError("gcloud returned invalid JSON metadata") from exc


def load_mapping(path: Path) -> dict[str, Any]:
    try:
        value: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise VerificationError(f"Unable to load YAML contract: {path}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"YAML contract must contain an object: {path}")
    return value


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def finding(identifier: str, passed: bool) -> Finding:
    return Finding(
        id=identifier,
        passed=passed,
        detail="matches contract" if passed else "missing or does not match contract",
    )


def integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_billing_account(value: str) -> str:
    candidate = value.strip()
    prefix = "billingAccounts/"
    account_id = candidate.removeprefix(prefix)
    if not re.fullmatch(r"[0-9A-F]{6}(?:-[0-9A-F]{6}){2}", account_id):
        raise VerificationError(
            "GCP_BILLING_ACCOUNT must be a Google Cloud billing account ID"
        )
    return f"{prefix}{account_id}"


def validate_billing(value: Any, project: str, billing_account: str) -> Finding:
    data = mapping(value)
    passed = (
        data.get("billingEnabled") is True
        and data.get("projectId") == project
        and data.get("billingAccountName") == billing_account
    )
    return finding("billing_account_linked", passed)


def validate_database(value: Any, project: str, contract: dict[str, Any]) -> Finding:
    data = mapping(value)
    firestore = mapping(contract.get("firestore"))
    expected_name = f"projects/{project}/databases/{firestore.get('database_id')}"
    passed = (
        data.get("name") == expected_name
        and data.get("locationId") == contract.get("region")
        and data.get("type") == "FIRESTORE_NATIVE"
        and data.get("deleteProtectionState") == "DELETE_PROTECTION_ENABLED"
    )
    return finding("firestore_database", passed)


def validate_vector_index(value: Any, contract: dict[str, Any]) -> Finding:
    firestore = mapping(contract.get("firestore"))
    base_index_ready = False
    source_index_ready = False
    for candidate in sequence(value):
        index = mapping(candidate)
        resource_name = str(index.get("name", ""))
        expected_collection_segment = f"/collectionGroups/{firestore.get('collection_group')}/"
        collection_matches = (
            index.get("collectionGroup") == firestore.get("collection_group")
            or expected_collection_segment in resource_name
        )
        if not collection_matches:
            continue
        if str(index.get("queryScope", "")).upper() != "COLLECTION":
            continue
        if str(index.get("state", "")).upper() != "READY":
            continue
        fields = [mapping(raw_field) for raw_field in sequence(index.get("fields"))]
        has_vector = False
        has_source_filter = False
        for field in fields:
            vector = mapping(field.get("vectorConfig"))
            try:
                dimensions = int(vector.get("dimension", 0))
            except (TypeError, ValueError):
                dimensions = 0
            if field.get("fieldPath") == firestore.get(
                "vector_field"
            ) and dimensions == firestore.get("vector_dimensions"):
                has_vector = True
            if field.get("fieldPath") == firestore.get("source_filter_field") and str(
                field.get("order", "")
            ).upper() == "ASCENDING":
                has_source_filter = True
        if has_vector and has_source_filter:
            source_index_ready = True
        elif has_vector:
            base_index_ready = True
    return finding("firestore_vector_index", base_index_ready and source_index_ready)


def validate_repository(value: Any, project: str, contract: dict[str, Any]) -> Finding:
    data = mapping(value)
    repository = mapping(contract.get("artifact_registry"))
    expected_name = (
        f"projects/{project}/locations/{contract.get('region')}/repositories/"
        f"{repository.get('repository')}"
    )
    passed = data.get("name") == expected_name and data.get("format") == "DOCKER"
    return finding("artifact_registry", passed)


def validate_cloud_run(value: Any, project: str, contract: dict[str, Any]) -> Finding:
    del project
    data = mapping(value)
    metadata = mapping(data.get("metadata"))
    spec = mapping(data.get("spec"))
    template = mapping(spec.get("template"))
    template_metadata = mapping(template.get("metadata"))
    annotations = mapping(template_metadata.get("annotations"))
    template_spec = mapping(template.get("spec"))
    containers = sequence(template_spec.get("containers"))
    container = mapping(containers[0]) if containers else {}
    env = {
        str(mapping(item).get("name")): mapping(item)
        for item in sequence(container.get("env"))
        if mapping(item).get("name")
    }
    cloud_run = mapping(contract.get("cloud_run"))
    firestore = mapping(contract.get("firestore"))
    required_secret_env = {
        "AZURE_OPENAI_API_BASE",
        "AZURE_OPENAI_API_LLM_KEY",
        "FLAMINGO_SAFETY_SALT",
    }
    secret_env_valid = all(
        bool(mapping(mapping(env.get(name)).get("valueFrom")).get("secretKeyRef"))
        for name in required_secret_env
    )
    min_scale = annotations.get("autoscaling.knative.dev/minScale", "0")
    max_scale = annotations.get("autoscaling.knative.dev/maxScale")
    status = mapping(data.get("status"))
    ready = any(
        mapping(condition).get("type") == "Ready" and mapping(condition).get("status") == "True"
        for condition in sequence(status.get("conditions"))
    )
    traffic = sequence(status.get("traffic"))
    active_traffic = [
        item
        for item in traffic
        if integer(mapping(item).get("percent")) == 100 and not mapping(item).get("tag")
    ]
    labels = mapping(metadata.get("labels"))
    passed = (
        metadata.get("name") == cloud_run.get("service")
        and labels.get("cloud.googleapis.com/location") == contract.get("region")
        and str(min_scale) == str(cloud_run.get("minimum_instances"))
        and str(max_scale) == str(cloud_run.get("maximum_instances"))
        and mapping(env.get("FIRESTORE_DATABASE_ID")).get("value") == firestore.get("database_id")
        and mapping(env.get("FLAMINGO_ENVIRONMENT")).get("value") == "production"
        and bool(mapping(env.get("FLAMINGO_ALLOWED_ORIGINS")).get("value"))
        and secret_env_valid
        and ready
        and len(active_traffic) == 1
    )
    return finding("cloud_run_service", passed)


def validate_public_invoker(value: Any) -> Finding:
    passed = any(
        mapping(binding).get("role") == "roles/run.invoker"
        and "allUsers" in sequence(mapping(binding).get("members"))
        for binding in sequence(mapping(value).get("bindings"))
    )
    return finding("cloud_run_public_invoker", passed)


def validate_secret(value: Any, secret: str, contract: dict[str, Any]) -> Finding:
    data = mapping(value)
    replication = mapping(data.get("replication"))
    user_managed = mapping(replication.get("userManaged"))
    locations = {
        mapping(replica).get("location") for replica in sequence(user_managed.get("replicas"))
    }
    passed = data.get("name", "").endswith(f"/secrets/{secret}") and locations == {
        contract.get("region")
    }
    return finding(f"secret_manager_{secret}", passed)


def validate_role(value: Any, role_id: str, expected_path: Path) -> Finding:
    data = mapping(value)
    expected = load_mapping(expected_path)
    actual_permissions = set(sequence(data.get("includedPermissions")))
    expected_permissions = set(sequence(expected.get("includedPermissions")))
    passed = data.get("name", "").endswith(f"/roles/{role_id}") and (
        actual_permissions == expected_permissions
    )
    return finding(f"iam_role_{role_id}", passed)


def checked_fetch(
    runner: JsonRunner,
    arguments: Sequence[str],
    identifier: str,
    findings: list[Finding],
) -> Any | None:
    try:
        return runner(arguments)
    except VerificationError:
        findings.append(
            Finding(id=identifier, passed=False, detail="resource metadata unavailable")
        )
        return None


def verify(
    *,
    project: str,
    billing_account: str,
    contract_path: Path,
    runner: JsonRunner,
) -> list[Finding]:
    contract = load_mapping(contract_path)
    expected_billing_account = normalize_billing_account(billing_account)
    region = str(contract["region"])
    firestore = mapping(contract["firestore"])
    repository = mapping(contract["artifact_registry"])
    cloud_run = mapping(contract["cloud_run"])
    secrets = [str(value) for value in sequence(mapping(contract["secret_manager"])["secrets"])]
    iam = mapping(contract["iam"])
    findings: list[Finding] = []

    billing = checked_fetch(
        runner,
        ["billing", "projects", "describe", project],
        "billing_account_linked",
        findings,
    )
    if billing is not None:
        findings.append(validate_billing(billing, project, expected_billing_account))

    database = checked_fetch(
        runner,
        [
            "firestore",
            "databases",
            "describe",
            "--project",
            project,
            "--database",
            str(firestore["database_id"]),
        ],
        "firestore_database",
        findings,
    )
    if database is not None:
        findings.append(validate_database(database, project, contract))
    indexes = checked_fetch(
        runner,
        [
            "firestore",
            "indexes",
            "composite",
            "list",
            "--project",
            project,
            "--database",
            str(firestore["database_id"]),
        ],
        "firestore_vector_index",
        findings,
    )
    if indexes is not None:
        findings.append(validate_vector_index(indexes, contract))

    artifact = checked_fetch(
        runner,
        [
            "artifacts",
            "repositories",
            "describe",
            str(repository["repository"]),
            "--project",
            project,
            "--location",
            region,
        ],
        "artifact_registry",
        findings,
    )
    if artifact is not None:
        findings.append(validate_repository(artifact, project, contract))

    service_arguments = [
        "run",
        "services",
        "describe",
        str(cloud_run["service"]),
        "--project",
        project,
        "--region",
        region,
    ]
    service = checked_fetch(runner, service_arguments, "cloud_run_service", findings)
    if service is not None:
        findings.append(validate_cloud_run(service, project, contract))
    service_policy = checked_fetch(
        runner,
        [
            "run",
            "services",
            "get-iam-policy",
            str(cloud_run["service"]),
            "--project",
            project,
            "--region",
            region,
        ],
        "cloud_run_public_invoker",
        findings,
    )
    if service_policy is not None:
        findings.append(validate_public_invoker(service_policy))

    for secret in secrets:
        value = checked_fetch(
            runner,
            ["secrets", "describe", secret, "--project", project],
            f"secret_manager_{secret}",
            findings,
        )
        if value is not None:
            findings.append(validate_secret(value, secret, contract))

    role_files = {
        str(iam["ingestion_role"]): contract_path.parent / "iam/ingestion-role.yaml",
        str(iam["deployer_role"]): contract_path.parent / "iam/deployer-role.yaml",
    }
    for role_id, role_file in role_files.items():
        value = checked_fetch(
            runner,
            ["iam", "roles", "describe", role_id, "--project", project],
            f"iam_role_{role_id}",
            findings,
        )
        if value is not None:
            findings.append(validate_role(value, role_id, role_file))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    parser.add_argument("--contract", type=Path, default=PROJECT_ROOT / "infra/contract.yaml")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        project = args.project or os.environ.get("GCP_PROJECT_ID", "").strip()
        if not project:
            raise VerificationError("--project or GCP_PROJECT_ID is required")
        billing_account = os.environ.get("GCP_BILLING_ACCOUNT", "")
        findings = verify(
            project=project,
            billing_account=billing_account,
            contract_path=args.contract,
            runner=GcloudJsonRunner(),
        )
    except VerificationError as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2
    payload = {
        "passed": all(item.passed for item in findings),
        "read_only": True,
        "findings": [asdict(item) for item in findings],
    }
    print(json.dumps(payload, indent=2 if args.pretty else None))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
