import subprocess

import pytest

from flamingo_bot.config import Settings
from flamingo_bot.errors import ConfigurationError, SourceValidationError
from flamingo_bot.sources import load_source_catalog, repository_revision, resolve_sources


def git(repository: str, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", repository, *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_repository_revision_requires_clean_reproducible_source(tmp_path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    git(str(repository), "init", "--quiet")
    git(str(repository), "config", "user.email", "test@example.org")
    git(str(repository), "config", "user.name", "Test")
    source = repository / "content.md"
    source.write_text("Versioned public content", encoding="utf-8")
    git(str(repository), "add", "content.md")
    git(str(repository), "commit", "--quiet", "-m", "Initial source")

    revision = repository_revision(repository)
    assert len(revision) == 40

    source.write_text("Uncommitted public content", encoding="utf-8")
    with pytest.raises(SourceValidationError, match="uncommitted changes"):
        repository_revision(repository)


def test_source_resolution_requires_explicit_repository_paths() -> None:
    catalog = load_source_catalog(Settings(_env_file=None).source_catalog_path)

    with pytest.raises(ConfigurationError, match="FLAMINGO_DOSSIER_REPO is required"):
        resolve_sources(Settings(_env_file=None), catalog)
