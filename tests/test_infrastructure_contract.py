from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str) -> dict[str, Any]:
    value: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def permissions(path: str) -> set[str]:
    raw = load_yaml(path).get("includedPermissions")
    assert isinstance(raw, list)
    assert all(isinstance(value, str) for value in raw)
    return set(raw)


def workflow_steps() -> list[dict[str, Any]]:
    workflow = load_yaml(".github/workflows/deploy.yml")
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    deploy = jobs.get("deploy")
    assert isinstance(deploy, dict)
    steps = deploy.get("steps")
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def step_by_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("id") == step_id)


def step_by_name(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


def test_fixed_gcp_resources_are_co_located_and_vector_contract_matches() -> None:
    contract = load_yaml("infra/contract.yaml")
    region = contract["region"]
    firestore = contract["firestore"]
    cloud_run = contract["cloud_run"]
    artifact_registry = contract["artifact_registry"]
    secret_manager = contract["secret_manager"]

    assert "project_id" not in contract
    assert "billing" not in contract
    assert region == "europe-west3"
    assert firestore == {
        "database_id": "flamingo-rag",
        "mode": "firestore-native",
        "delete_protection": True,
        "collection_group": "chunks",
        "vector_field": "embedding",
        "source_filter_field": "source_id",
        "vector_dimensions": 1024,
        "distance_measure": "cosine",
    }
    assert cloud_run["minimum_instances"] == 0
    assert cloud_run["maximum_instances"] == 5
    assert artifact_registry == {"repository": "flamingo-containers", "format": "docker"}
    assert secret_manager["replication"] == "user-managed"
    assert secret_manager["locations"] == [region]

    vector_index: Any = json.loads(
        Path("infra/firestore/vector-index.json").read_text(encoding="utf-8")
    )
    assert vector_index == [
        {
            "field-path": firestore["vector_field"],
            "vector-config": {"dimension": firestore["vector_dimensions"], "flat": {}},
        }
    ]
    source_vector_index: Any = json.loads(
        Path("infra/firestore/vector-index-by-source.json").read_text(encoding="utf-8")
    )
    assert source_vector_index == [
        {"field-path": firestore["source_filter_field"], "order": "ascending"},
        {
            "field-path": firestore["vector_field"],
            "vector-config": {"dimension": firestore["vector_dimensions"], "flat": {}},
        },
    ]


def test_ingestion_role_can_publish_and_roll_back_but_never_delete() -> None:
    actual = permissions("infra/iam/ingestion-role.yaml")

    assert actual == {
        "datastore.databases.get",
        "datastore.entities.create",
        "datastore.entities.get",
        "datastore.entities.list",
        "datastore.entities.update",
    }
    assert "datastore.entities.delete" not in actual


def test_deployer_role_supports_release_without_destructive_cloud_run_access() -> None:
    actual = permissions("infra/iam/deployer-role.yaml")
    required = {
        "run.operations.get",
        "run.revisions.get",
        "run.revisions.list",
        "run.services.create",
        "run.services.get",
        "run.services.getIamPolicy",
        "run.services.list",
        "run.services.setIamPolicy",
        "run.services.update",
    }

    assert actual == required
    assert not any(".delete" in permission for permission in actual)
    assert not any(".ssh" in permission for permission in actual)
    assert not any(permission.startswith("run.jobs.") for permission in actual)


def test_deployment_uses_wif_and_smokes_candidate_before_traffic_promotion() -> None:
    workflow = load_yaml(".github/workflows/deploy.yml")
    contract = load_yaml("infra/contract.yaml")
    steps = workflow_steps()
    auth = step_by_id(steps, "auth")
    candidate = step_by_id(steps, "candidate")
    smoke = step_by_id(steps, "smoke")
    promote = step_by_name(steps, "Promote validated candidate")
    reject = step_by_name(steps, "Remove failed candidate route")

    workflow_env = workflow["env"]
    assert workflow_env["REGION"] == contract["region"]
    assert workflow_env["SERVICE"] == contract["cloud_run"]["service"]
    assert workflow_env["DATABASE"] == contract["firestore"]["database_id"]
    assert workflow_env["REPOSITORY"] == contract["artifact_registry"]["repository"]

    assert auth["uses"] == "google-github-actions/auth@v3"
    assert "workload_identity_provider" in auth["with"]
    assert "service_account" in auth["with"]
    assert "credentials_json" not in auth["with"]

    candidate_run = str(candidate["run"])
    smoke_run = str(smoke["run"])
    promote_run = str(promote["run"])
    reject_run = str(reject["run"])
    assert "--no-traffic" in candidate_run
    assert f"--tag {contract['cloud_run']['candidate_tag']}" in candidate_run
    assert "--allow-unauthenticated" in candidate_run
    assert "evals/smoke.yaml" in smoke_run
    assert "--to-revisions" in promote_run
    assert "=100" in promote_run
    assert "--remove-tags candidate" in promote_run
    assert "--remove-tags candidate" in reject_run
    assert promote["if"] == "steps.smoke.outcome == 'success'"
    assert reject["if"] == "steps.smoke.outcome == 'failure'"
    assert steps.index(candidate) < steps.index(smoke) < steps.index(promote)


def test_production_image_defaults_to_non_root_runtime() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "useradd --system --uid 10001" in dockerfile
    assert "USER 10001" in dockerfile


def test_environment_examples_are_portable_and_private() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")

    assert not re.search(r"/(?:home|Users)/[^/\s]+/", example)
    assert not re.search(r"[0-9A-F]{6}(?:-[0-9A-F]{6}){2}", example)
    assert "GCP_PROJECT_ID=\n" in example
    assert "GCP_BILLING_ACCOUNT=\n" in example
    assert "FLAMINGO_DOSSIER_REPO=\n" in example
    assert "FLAMINGO_REVOLUTION_REPO=\n" in example
    assert "DIASPORA_ZBARKON_REPO=\n" in example


def test_secret_bearing_environment_variants_are_excluded() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env*" in gitignore
    assert "!.env.example" in gitignore
    assert ".env*" in dockerignore
    assert "!.env.example" not in dockerignore
