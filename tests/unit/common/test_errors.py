"""Unit tests for the application error hierarchy (``src.common.errors``)."""

from __future__ import annotations

import pytest

from src.common import errors
from src.common.errors import (
    AppError,
    BedrockError,
    BedrockThrottledError,
    ComprehendError,
    ConfigError,
    ContactLensError,
    CrawlerError,
    CrmApiError,
    DynamoAccessError,
    EmbeddingError,
    FetchTimeoutError,
    NotFoundError,
    ObjectNotFoundError,
    ParseError,
    ProfileLookupError,
    ResponseParseError,
    RobotsDisallowedError,
    RobotsNotLoadedError,
    S3AccessError,
    SearchError,
    SecretsError,
    SessionNotFoundError,
    TimeoutBudgetExceeded,
    UnauthorizedError,
    ValidationError,
)

# All concrete error classes (excluding the AppError base) in the hierarchy.
ALL_ERROR_CLASSES: list[type[AppError]] = [
    DynamoAccessError,
    S3AccessError,
    ObjectNotFoundError,
    BedrockError,
    BedrockThrottledError,
    EmbeddingError,
    ComprehendError,
    ContactLensError,
    SecretsError,
    CrmApiError,
    CrawlerError,
    RobotsDisallowedError,
    RobotsNotLoadedError,
    FetchTimeoutError,
    ParseError,
    SearchError,
    ResponseParseError,
    SessionNotFoundError,
    ProfileLookupError,
    ConfigError,
    ValidationError,
    UnauthorizedError,
    NotFoundError,
    TimeoutBudgetExceeded,
]

# Subclasses that should be retryable.
RETRYABLE_CLASSES: list[type[AppError]] = [
    BedrockThrottledError,
    FetchTimeoutError,
    TimeoutBudgetExceeded,
]

# Expected inheritance pairs (subclass, expected ancestor).
INHERITANCE_PAIRS: list[tuple[type[AppError], type[AppError]]] = [
    (ObjectNotFoundError, S3AccessError),
    (BedrockThrottledError, BedrockError),
    (EmbeddingError, BedrockError),
    (RobotsDisallowedError, CrawlerError),
    (RobotsNotLoadedError, CrawlerError),
    (FetchTimeoutError, CrawlerError),
    (ParseError, CrawlerError),
]


def test_app_error_is_exception_subclass() -> None:
    assert issubclass(AppError, Exception)


def test_module_exports_all_error_classes() -> None:
    """AppError + all subclasses are exported via __all__."""
    assert len(errors.__all__) == len(ALL_ERROR_CLASSES) + 1  # +1 for AppError base
    assert "AppError" in errors.__all__
    assert len(ALL_ERROR_CLASSES) == 24
    for cls in ALL_ERROR_CLASSES:
        assert cls.__name__ in errors.__all__


@pytest.mark.parametrize("cls", ALL_ERROR_CLASSES)
def test_subclasses_derive_from_app_error(cls: type[AppError]) -> None:
    assert issubclass(cls, AppError)
    assert issubclass(cls, Exception)


@pytest.mark.parametrize("cls", [AppError, *ALL_ERROR_CLASSES])
def test_instantiation_and_attributes(cls: type[AppError]) -> None:
    err = cls("something went wrong")
    assert isinstance(err, cls)
    assert isinstance(err, AppError)
    assert err.message == "something went wrong"
    assert isinstance(err.code, str)
    assert err.code != ""
    # The exception message is forwarded to the base Exception.
    assert str(err) == "something went wrong"


@pytest.mark.parametrize("cls", [AppError, *ALL_ERROR_CLASSES])
def test_default_message_is_empty_string(cls: type[AppError]) -> None:
    err = cls()
    assert err.message == ""
    assert isinstance(err.code, str)


def test_code_override_via_constructor() -> None:
    err = AppError("boom", code="CUSTOM_CODE")
    assert err.code == "CUSTOM_CODE"
    assert err.message == "boom"


@pytest.mark.parametrize("cls", ALL_ERROR_CLASSES)
def test_each_class_has_distinct_default_code(cls: type[AppError]) -> None:
    # The class-level default code must not be the generic base code.
    assert cls.code != AppError.code or cls is AppError


@pytest.mark.parametrize(("sub", "ancestor"), INHERITANCE_PAIRS)
def test_inheritance_chains(sub: type[AppError], ancestor: type[AppError]) -> None:
    assert issubclass(sub, ancestor)
    instance = sub("x")
    assert isinstance(instance, ancestor)
    assert isinstance(instance, AppError)


@pytest.mark.parametrize("cls", RETRYABLE_CLASSES)
def test_retryable_errors(cls: type[AppError]) -> None:
    assert cls.retryable is True
    assert cls("x").retryable is True


@pytest.mark.parametrize(
    "cls",
    [c for c in ALL_ERROR_CLASSES if c not in RETRYABLE_CLASSES],
)
def test_non_retryable_errors(cls: type[AppError]) -> None:
    assert cls.retryable is False
    assert cls("x").retryable is False


def test_app_error_base_is_non_retryable() -> None:
    assert AppError.retryable is False


def test_can_be_raised_and_caught_as_app_error() -> None:
    with pytest.raises(AppError) as exc_info:
        raise BedrockThrottledError("throttled")
    assert exc_info.value.code == "BEDROCK_THROTTLED"
    assert exc_info.value.retryable is True


def test_repr_contains_code_and_message() -> None:
    err = DynamoAccessError("read failed")
    rendered = repr(err)
    assert "DynamoAccessError" in rendered
    assert "DYNAMO_ACCESS_ERROR" in rendered
    assert "read failed" in rendered
