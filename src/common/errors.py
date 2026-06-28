"""Application error hierarchy for the au Jibun Bank AI Agent.

All application-level exceptions derive from :class:`AppError`, which carries a
machine-readable ``code`` and a human-readable ``message``. Each error declares a
``retryable`` class attribute so that callers can drive retry / back-off behaviour
purely from the exception type (throttling and timeout style failures are
retryable; everything else is terminal by default).

Python 3.12 typing style is used throughout (``str | None`` instead of
``Optional[str]``).
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application errors.

    Attributes:
        code: Stable machine-readable error code (defaults to the class name).
        message: Human-readable description of the failure.
        retryable: Whether the operation that raised this error may be retried.
    """

    #: Default error code; subclasses may override.
    code: str = "APP_ERROR"
    #: Whether the failing operation is safe to retry.
    retryable: bool = False

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        self.message: str = message
        if code is not None:
            self.code = code
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


# --------------------------------------------------------------------------- #
# DynamoDB
# --------------------------------------------------------------------------- #
class DynamoAccessError(AppError):
    """Raised when a DynamoDB read/write operation fails."""

    code = "DYNAMO_ACCESS_ERROR"


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #
class S3AccessError(AppError):
    """Raised when an S3 operation fails."""

    code = "S3_ACCESS_ERROR"


class ObjectNotFoundError(S3AccessError):
    """Raised when a requested S3 object does not exist."""

    code = "OBJECT_NOT_FOUND"


# --------------------------------------------------------------------------- #
# Bedrock
# --------------------------------------------------------------------------- #
class BedrockError(AppError):
    """Raised when an Amazon Bedrock invocation fails."""

    code = "BEDROCK_ERROR"


class BedrockThrottledError(BedrockError):
    """Raised when Amazon Bedrock throttles a request (retryable)."""

    code = "BEDROCK_THROTTLED"
    retryable = True


class EmbeddingError(BedrockError):
    """Raised when embedding generation via Bedrock fails."""

    code = "EMBEDDING_ERROR"


# --------------------------------------------------------------------------- #
# Comprehend / Contact Lens
# --------------------------------------------------------------------------- #
class ComprehendError(AppError):
    """Raised when an Amazon Comprehend operation fails."""

    code = "COMPREHEND_ERROR"


class ContactLensError(AppError):
    """Raised when an Amazon Connect Contact Lens operation fails."""

    code = "CONTACT_LENS_ERROR"


# --------------------------------------------------------------------------- #
# Secrets / CRM
# --------------------------------------------------------------------------- #
class SecretsError(AppError):
    """Raised when a Secrets Manager operation fails."""

    code = "SECRETS_ERROR"


class CrmApiError(AppError):
    """Raised when a call to the external CRM API fails."""

    code = "CRM_API_ERROR"


# --------------------------------------------------------------------------- #
# Crawler
# --------------------------------------------------------------------------- #
class CrawlerError(AppError):
    """Base class for web crawler failures."""

    code = "CRAWLER_ERROR"


class RobotsDisallowedError(CrawlerError):
    """Raised when robots.txt disallows crawling a URL."""

    code = "ROBOTS_DISALLOWED"


class RobotsNotLoadedError(CrawlerError):
    """Raised when robots.txt could not be loaded before crawling."""

    code = "ROBOTS_NOT_LOADED"


class FetchTimeoutError(CrawlerError):
    """Raised when an HTTP fetch times out (retryable)."""

    code = "FETCH_TIMEOUT"
    retryable = True


class ParseError(CrawlerError):
    """Raised when crawled content cannot be parsed."""

    code = "PARSE_ERROR"


# --------------------------------------------------------------------------- #
# Search / Response / Session / Profile
# --------------------------------------------------------------------------- #
class SearchError(AppError):
    """Raised when a vector / keyword search operation fails."""

    code = "SEARCH_ERROR"


class CacheConsistencyError(AppError):
    """Raised when the vector cache matrix and metadata row counts disagree.

    Guards the write path so a drifted matrix/meta pair is never persisted to
    S3, and the patch path so updates are never applied on a corrupt base.
    """

    code = "CACHE_CONSISTENCY_ERROR"


class ResponseParseError(AppError):
    """Raised when a model or service response cannot be parsed."""

    code = "RESPONSE_PARSE_ERROR"


class SessionNotFoundError(AppError):
    """Raised when a conversation session cannot be found."""

    code = "SESSION_NOT_FOUND"


class ProfileLookupError(AppError):
    """Raised when a customer profile lookup fails."""

    code = "PROFILE_LOOKUP_ERROR"


# --------------------------------------------------------------------------- #
# Config / Validation / AuthZ / Generic
# --------------------------------------------------------------------------- #
class ConfigError(AppError):
    """Raised when configuration (env / SSM) is missing or invalid."""

    code = "CONFIG_ERROR"


class ValidationError(AppError):
    """Raised when input validation fails."""

    code = "VALIDATION_ERROR"


class UnauthorizedError(AppError):
    """Raised when an operation is not authorized."""

    code = "UNAUTHORIZED"


class NotFoundError(AppError):
    """Raised when a generic resource cannot be found."""

    code = "NOT_FOUND"


class TimeoutBudgetExceeded(AppError):
    """Raised when an operation exceeds its allotted time budget (retryable)."""

    code = "TIMEOUT_BUDGET_EXCEEDED"
    retryable = True


__all__ = [
    "AppError",
    "BedrockError",
    "BedrockThrottledError",
    "ComprehendError",
    "ConfigError",
    "ContactLensError",
    "CrawlerError",
    "CrmApiError",
    "DynamoAccessError",
    "EmbeddingError",
    "FetchTimeoutError",
    "NotFoundError",
    "ObjectNotFoundError",
    "ParseError",
    "ProfileLookupError",
    "ResponseParseError",
    "RobotsDisallowedError",
    "RobotsNotLoadedError",
    "S3AccessError",
    "SearchError",
    "SecretsError",
    "SessionNotFoundError",
    "TimeoutBudgetExceeded",
    "UnauthorizedError",
    "ValidationError",
]
