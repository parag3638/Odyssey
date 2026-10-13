"""Typed app errors and the single BACKEND.md §3 shape they serialise to."""

from __future__ import annotations

import pytest

from src.lib.errors import (
    AppError,
    Conflict,
    DependencyUnavailable,
    Forbidden,
    NotFound,
    RateLimited,
    RuleBroken,
    SpendCapReached,
    TooLarge,
)
from src.main import error_body

CASES: list[tuple[type[AppError], int, str]] = [
    (NotFound, 404, "not_found"),
    (Forbidden, 403, "forbidden"),
    (Conflict, 409, "conflict"),
    (RuleBroken, 422, "rule_broken"),
    (TooLarge, 413, "too_large"),
    (RateLimited, 429, "rate_limited"),
    (SpendCapReached, 429, "spend_cap_reached"),
    (DependencyUnavailable, 503, "dependency_unavailable"),
]


@pytest.mark.parametrize("error_cls,status,code", CASES)
def test_each_typed_error_carries_its_documented_status_and_code(
    error_cls: type[AppError], status: int, code: str
) -> None:
    error = error_cls("some message")

    assert error.status == status
    assert error.code == code
    assert error.message == "some message"


def test_fields_default_to_none() -> None:
    assert NotFound("missing").fields is None


def test_fields_carry_through_when_given() -> None:
    error = RuleBroken("Bad input.", fields={"quantity": "Must be 1 or more."})

    assert error.fields == {"quantity": "Must be 1 or more."}


def test_error_body_omits_fields_key_when_none_given() -> None:
    body = error_body("not_found", "Not found.", "req_1", None)

    assert body == {"error": {"code": "not_found", "message": "Not found.", "requestId": "req_1"}}


def test_error_body_includes_fields_key_when_given() -> None:
    body = error_body("rule_broken", "Bad input.", "req_1", {"quantity": "Must be 1 or more."})

    assert body["error"]["fields"] == {"quantity": "Must be 1 or more."}
