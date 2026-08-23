from pathlib import Path

import pytest

from flamingo_bot.config import Settings, load_model_config
from flamingo_bot.errors import ConfigurationError


def test_model_config_contains_only_selected_contracts() -> None:
    config = load_model_config(Path("openai.yaml"))

    assert config.chat.deployment == "gpt-5.6-luna"
    assert config.chat.reasoning_effort == "medium"
    assert config.embedding.deployment == "text-embedding-3-large"
    assert config.embedding.dimensions == 1024


def test_azure_credentials_are_required_only_when_requested() -> None:
    settings = Settings(
        _env_file=None,
        azure_openai_api_base=None,
        azure_openai_api_llm_key=None,
    )

    with pytest.raises(ConfigurationError, match="AZURE_OPENAI_API_BASE"):
        settings.require_azure_credentials()


def test_source_repositories_have_no_machine_specific_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.flamingo_dossier_repo is None
    assert settings.flamingo_revolution_repo is None
    assert settings.diaspora_zbarkon_repo is None


def test_blank_source_repository_values_are_treated_as_unset() -> None:
    settings = Settings(_env_file=None, flamingo_dossier_repo="  ")

    assert settings.flamingo_dossier_repo is None


def test_allowed_origins_are_normalized() -> None:
    settings = Settings(
        _env_file=None,
        flamingo_allowed_origins="https://example.org/, http://localhost:5173",
    )

    assert settings.allowed_origins == ("https://example.org", "http://localhost:5173")


def test_wildcard_or_path_is_not_accepted_as_browser_origin() -> None:
    wildcard = Settings(_env_file=None, flamingo_allowed_origins="*")
    path = Settings(_env_file=None, flamingo_allowed_origins="https://example.org/widget")

    with pytest.raises(ConfigurationError, match=r"HTTP\(S\) origins"):
        _ = wildcard.allowed_origins
    with pytest.raises(ConfigurationError, match=r"HTTP\(S\) origins"):
        _ = path.allowed_origins


def test_azure_base_must_be_https_resource_origin() -> None:
    settings = Settings(
        _env_file=None,
        azure_openai_api_base="http://example.org/openai",
        azure_openai_api_llm_key="test",
    )

    with pytest.raises(ConfigurationError, match="HTTPS Azure resource origin"):
        settings.require_azure_credentials()
