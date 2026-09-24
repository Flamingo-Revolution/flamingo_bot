"""Typed configuration loaded without import-time network or process side effects."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from flamingo_bot.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ChatModelConfig(BaseModel):
    deployment: str
    api_type: Literal["azure"] = "azure"
    api_version: str
    timeout_seconds: float = Field(gt=0, le=300)
    max_output_tokens: int = Field(gt=0, le=128_000)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"]
    text_verbosity: Literal["low", "medium", "high"] = "medium"


class EmbeddingModelConfig(BaseModel):
    deployment: str
    api_type: Literal["azure"] = "azure"
    api_version: str
    timeout_seconds: float = Field(gt=0, le=300)
    dimensions: int = Field(gt=0, le=2048)
    batch_size: int = Field(gt=0, le=2048)


class ModelConfig(BaseModel):
    chat: ChatModelConfig
    embedding: EmbeddingModelConfig
    #: Short non-streaming call that rewrites a follow-up into a standalone
    #: search query. It uses the same Responses contract as ``chat`` with a
    #: cheaper effort and a tighter deadline.
    condense: ChatModelConfig


class Settings(BaseSettings):
    """Runtime and ingestion settings.

    Azure settings are optional at parse time so a dry run can validate local
    source content without model credentials. Model-backed startup paths call
    :meth:`require_azure_credentials` explicitly.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    azure_openai_api_base: str | None = None
    azure_openai_api_llm_key: SecretStr | None = None

    gcp_project_id: str | None = None
    firestore_database_id: str = "flamingo-rag"
    #: Quota counters live in their own database because Firestore IAM cannot scope
    #: a grant to one collection. Keeping them apart is what lets the serving
    #: identity write its counters while the knowledge corpus stays read-only to it.
    flamingo_quota_database_id: str = "flamingo-quotas"
    gcp_impersonate_service_account: str | None = None

    flamingo_dossier_repo: Path | None = None
    flamingo_revolution_repo: Path | None = None
    diaspora_zbarkon_repo: Path | None = None
    flamingo_map_repo: Path | None = None

    flamingo_environment: Literal["development", "test", "production"] = "development"
    flamingo_allowed_origins: str = (
        "http://localhost:5173,http://localhost:4321,"
        "https://flamingorevolution.eu,https://www.flamingorevolution.eu,"
        "https://diaspora-zbarkon.com,https://www.diaspora-zbarkon.com"
    )
    flamingo_retrieval_candidates: int = Field(default=7, ge=5, le=7)
    flamingo_relevance_distance_threshold: float = Field(default=0.65, gt=0, le=2)
    #: Earlier turns replayed by the client that the service is willing to use.
    #: Zero disables conversation memory and restores single-question behavior.
    flamingo_history_turns: int = Field(default=6, ge=0, le=16)
    flamingo_history_chars: int = Field(default=4000, ge=200, le=20_000)
    flamingo_chat_concurrency: int = Field(default=8, ge=1, le=100)
    flamingo_rate_limit_requests: int = Field(default=20, ge=1, le=10_000)
    flamingo_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    #: Durable per-visitor allowance, shared across every serving instance.
    flamingo_client_quota_requests: int = Field(default=10, ge=1, le=10_000)
    flamingo_client_quota_window_seconds: int = Field(default=3_600, ge=60, le=604_800)
    #: Durable service-wide budget. This is the ceiling on what a day can cost.
    flamingo_daily_quota_requests: int = Field(default=100, ge=1, le=1_000_000)
    flamingo_daily_quota_window_seconds: int = Field(default=86_400, ge=60, le=604_800)
    #: Trailing ``X-Forwarded-For`` entries appended by trusted infrastructure that
    #: are not the caller. Cloud Run reached directly on its ``run.app`` URL appends
    #: only the client address, so nothing trails it and the default is zero. Behind
    #: an external HTTPS load balancer the balancer adds its own hop; set this to 1.
    flamingo_trusted_proxy_hops: int = Field(default=0, ge=0, le=4)
    flamingo_safety_salt: SecretStr | None = None

    model_config_path: Path = PROJECT_ROOT / "openai.yaml"
    source_catalog_path: Path = PROJECT_ROOT / "config" / "sources.yaml"
    flamingo_widget_dir: Path = PROJECT_ROOT / "frontend" / "dist"

    @field_validator(
        "flamingo_dossier_repo",
        "flamingo_revolution_repo",
        "diaspora_zbarkon_repo",
        "flamingo_map_repo",
        mode="before",
    )
    @classmethod
    def expand_optional_path(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return Path(stripped).expanduser() if stripped else None
        return value

    @field_validator(
        "model_config_path",
        "source_catalog_path",
        "flamingo_widget_dir",
        mode="before",
    )
    @classmethod
    def expand_path(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        origins = tuple(
            origin.strip().rstrip("/")
            for origin in self.flamingo_allowed_origins.split(",")
            if origin.strip()
        )
        for origin in origins:
            parsed = urlparse(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise ConfigurationError(
                    "FLAMINGO_ALLOWED_ORIGINS must contain only HTTP(S) origins"
                )
        return origins

    def require_azure_credentials(self) -> tuple[str, str]:
        if not self.azure_openai_api_base:
            raise ConfigurationError("AZURE_OPENAI_API_BASE is required for model access")
        parsed = urlparse(self.azure_openai_api_base)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError("AZURE_OPENAI_API_BASE must be an HTTPS Azure resource origin")
        if self.azure_openai_api_llm_key is None:
            raise ConfigurationError("AZURE_OPENAI_API_LLM_KEY is required for model access")
        key = self.azure_openai_api_llm_key.get_secret_value().strip()
        if not key:
            raise ConfigurationError("AZURE_OPENAI_API_LLM_KEY cannot be empty")
        return self.azure_openai_api_base.rstrip("/"), key


def load_model_config(path: Path) -> ModelConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Unable to read model configuration: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML model configuration: {path}") from exc
    try:
        return ModelConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid model configuration schema: {path}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
