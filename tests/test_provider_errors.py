"""Tests for app/services/provider_errors.py (docs/TASKS_SCHEDULER_OPS.md T3, design decision 4).

Every case is built from the REAL exception class of the installed SDK, constructed the way the
SDK itself constructs it (`response=httpx.Response(...)` plus the parsed body) — a hand-rolled
object with just `status_code` would pass here and still misclassify what a provider really sends.
"""

import anthropic
import httpx
import openai
import pytest
from google.genai import errors as genai_errors

from app.services.provider_errors import ErrorCategory, classify_provider_error

_REQUEST = httpx.Request("POST", "https://provider.example/v1")


def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=_REQUEST)


def _openai(cls, status: int, error: dict):
    response = _response(status, {"error": error})
    return cls(error["message"], response=response, body=error)


def _anthropic(cls, status: int, error_type: str, message: str):
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    return cls(message, response=_response(status, body), body=body)


def _google(cls, code: int, status: str, message: str):
    return cls(code, {"error": {"code": code, "message": message, "status": status}})


CASES = [
    # --- billing -------------------------------------------------------------------------
    (
        "openai 429 insufficient_quota",
        _openai(openai.RateLimitError, 429, {"message": "You exceeded your current quota, please check your plan and billing details.", "type": "insufficient_quota", "code": "insufficient_quota"}),
        ErrorCategory.BILLING,
    ),
    (
        "anthropic 400 credit balance",
        _anthropic(anthropic.BadRequestError, 400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."),
        ErrorCategory.BILLING,
    ),
    (
        "openai-compatible 402 insufficient balance (DeepSeek)",
        _openai(openai.APIStatusError, 402, {"message": "Insufficient Balance", "type": "unknown_error", "code": "invalid_request_error"}),
        ErrorCategory.BILLING,
    ),
    (
        "google 429 prepayment credits depleted",
        _google(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED", "Your prepayment credits are depleted. Please go to AI Studio to manage your project and billing."),
        ErrorCategory.BILLING,
    ),
    (
        "billing reported as 403 beats auth",
        _openai(openai.PermissionDeniedError, 403, {"message": "Your team has no credits: purchase more credits to continue", "type": "x", "code": None}),
        ErrorCategory.BILLING,
    ),
    # --- rate limit ----------------------------------------------------------------------
    (
        "openai 429 rate_limit_exceeded",
        _openai(openai.RateLimitError, 429, {"message": "Rate limit reached for requests", "type": "requests", "code": "rate_limit_exceeded"}),
        ErrorCategory.RATE_LIMIT,
    ),
    (
        "anthropic 429",
        _anthropic(anthropic.RateLimitError, 429, "rate_limit_error", "Number of request tokens has exceeded your per-minute rate limit"),
        ErrorCategory.RATE_LIMIT,
    ),
    (
        "google 429 per-minute quota is a rate limit, not billing",
        _google(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED", "Quota exceeded for metric: generate_content_requests_per_minute"),
        ErrorCategory.RATE_LIMIT,
    ),
    # --- auth ----------------------------------------------------------------------------
    (
        "openai 401",
        _openai(openai.AuthenticationError, 401, {"message": "Incorrect API key provided", "type": "invalid_request_error", "code": "invalid_api_key"}),
        ErrorCategory.AUTH,
    ),
    (
        "anthropic 401",
        _anthropic(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key"),
        ErrorCategory.AUTH,
    ),
    (
        "google 403",
        _google(genai_errors.ClientError, 403, "PERMISSION_DENIED", "API key not valid"),
        ErrorCategory.AUTH,
    ),
    # --- transient -----------------------------------------------------------------------
    (
        "openai 500",
        _openai(openai.InternalServerError, 500, {"message": "The server had an error", "type": "server_error", "code": None}),
        ErrorCategory.TRANSIENT,
    ),
    (
        "anthropic 529 overloaded",
        _anthropic(anthropic.APIStatusError, 529, "overloaded_error", "Overloaded"),
        ErrorCategory.TRANSIENT,
    ),
    (
        "google 503",
        _google(genai_errors.ServerError, 503, "UNAVAILABLE", "The model is overloaded"),
        ErrorCategory.TRANSIENT,
    ),
    ("openai timeout", openai.APITimeoutError(request=_REQUEST), ErrorCategory.TRANSIENT),
    ("openai connection error", openai.APIConnectionError(request=_REQUEST), ErrorCategory.TRANSIENT),
    ("anthropic timeout", anthropic.APITimeoutError(request=_REQUEST), ErrorCategory.TRANSIENT),
    ("httpx read timeout (google-genai lets it through raw)", httpx.ReadTimeout("timed out", request=_REQUEST), ErrorCategory.TRANSIENT),
    ("httpx connect error", httpx.ConnectError("refused", request=_REQUEST), ErrorCategory.TRANSIENT),
    ("builtin TimeoutError", TimeoutError("timed out"), ErrorCategory.TRANSIENT),
    # --- invalid request -----------------------------------------------------------------
    (
        "openai 400",
        _openai(openai.BadRequestError, 400, {"message": "Unsupported parameter", "type": "invalid_request_error", "code": None}),
        ErrorCategory.INVALID_REQUEST,
    ),
    (
        "anthropic 404 model not found",
        _anthropic(anthropic.NotFoundError, 404, "not_found_error", "model: claude-nope"),
        ErrorCategory.INVALID_REQUEST,
    ),
    (
        "google 400",
        _google(genai_errors.ClientError, 400, "INVALID_ARGUMENT", "Request contains an invalid argument"),
        ErrorCategory.INVALID_REQUEST,
    ),
    # --- unknown -------------------------------------------------------------------------
    ("plain ValueError", ValueError("adapter returned garbage"), ErrorCategory.UNKNOWN),
]


@pytest.mark.parametrize("error,expected", [(case[1], case[2]) for case in CASES], ids=[case[0] for case in CASES])
def test_classify_provider_error(error, expected):
    assert classify_provider_error(error) is expected


def test_a_5xx_is_never_billing_whatever_its_text_says():
    error = _openai(openai.InternalServerError, 500, {"message": "insufficient balance in upstream cache", "type": "server_error", "code": None})

    assert classify_provider_error(error) is ErrorCategory.TRANSIENT
