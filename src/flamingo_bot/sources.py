"""Source catalog loading and repository discovery."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from flamingo_bot.config import Settings
from flamingo_bot.errors import ConfigurationError, SourceValidationError


class SourceRule(BaseModel):
    parser: str
    include: list[str]
    exclude: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class SourceDefinition(BaseModel):
    id: str
    label: str
    repository_setting: str
    revision_key: str | None = None
    language: str
    base_url: str
    rules: list[SourceRule]


class SourceCatalog(BaseModel):
    version: int
    sources: list[SourceDefinition]


class ResolvedSource(BaseModel):
    definition: SourceDefinition
    repository_path: Path
    revision: str


def load_source_catalog(path: Path) -> SourceCatalog:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Unable to read source catalog: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML source catalog: {path}") from exc
    try:
        return SourceCatalog.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid source catalog schema: {path}") from exc


def repository_revision(repository_path: Path) -> str:
    try:
        revision_result = subprocess.run(
            ["git", "-C", str(repository_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status_result = subprocess.run(
            ["git", "-C", str(repository_path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceValidationError(
            f"Unable to resolve Git revision for source repository: {repository_path}"
        ) from exc
    if status_result.stdout.strip():
        raise SourceValidationError(f"Source repository has uncommitted changes: {repository_path}")
    revision = revision_result.stdout.strip()
    if len(revision) != 40:
        raise SourceValidationError(f"Unexpected Git revision for {repository_path}")
    return revision


def resolve_sources(settings: Settings, catalog: SourceCatalog) -> list[ResolvedSource]:
    resolved: list[ResolvedSource] = []
    for definition in catalog.sources:
        if not hasattr(settings, definition.repository_setting):
            raise ConfigurationError(f"Unknown repository setting: {definition.repository_setting}")
        configured_path = getattr(settings, definition.repository_setting)
        if configured_path is None:
            variable = definition.repository_setting.upper()
            raise ConfigurationError(f"{variable} is required for ingestion")
        repository_path = configured_path.resolve()
        if not repository_path.is_dir():
            raise SourceValidationError(f"Source repository does not exist: {repository_path}")
        resolved.append(
            ResolvedSource(
                definition=definition,
                repository_path=repository_path,
                revision=repository_revision(repository_path),
            )
        )
    return resolved


def discover_rule_files(repository_path: Path, rule: SourceRule) -> list[Path]:
    excluded: set[Path] = set()
    for pattern in rule.exclude:
        excluded.update(path.resolve() for path in repository_path.glob(pattern) if path.is_file())

    discovered: set[Path] = set()
    for pattern in rule.include:
        discovered.update(
            path.resolve() for path in repository_path.glob(pattern) if path.is_file()
        )
    return sorted(discovered - excluded)
