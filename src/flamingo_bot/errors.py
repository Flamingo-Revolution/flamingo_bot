"""Domain-specific failures with stable operational classifications."""


class FlamingoBotError(Exception):
    """Base class for expected application failures."""


class ConfigurationError(FlamingoBotError):
    """Required configuration is absent or invalid."""


class SourceValidationError(FlamingoBotError):
    """A configured source repository or document is invalid."""


class ProviderError(FlamingoBotError):
    """An external model or storage provider failed."""


class PublicationError(FlamingoBotError):
    """A staged ingestion generation could not be validated or activated."""


class RollbackError(FlamingoBotError):
    """A requested knowledge-generation rollback could not be validated or applied."""
