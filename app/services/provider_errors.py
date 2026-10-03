"""Classify an exception raised by a provider adapter (docs/TASKS_SCHEDULER_OPS.md T3, design
decisions 4-5).

One function for all providers instead of one per adapter: the adapters let the raw SDK exception
propagate (app/adapters/base.py), so the shapes differ — OpenAI-compatible SDKs carry
`status_code` + `code` + `body` (the inner error dict), Anthropic carries `status_code` + `body`
(`{"type": "error", "error": {...}}`) and no `code`, google-genai carries an int `code` plus
`status`/`message`/`details`. Detection is duck-typed on those attributes and on the message text,
never on SDK classes, so this module imports no provider SDK and a new SDK version means editing
this file only.

The category is NOT stored (design decision 12): the worker derives it at the moment of failure,
acts on it, and writes it as a `[category]` prefix into `run_queue.last_error`.
"""

import enum
import json

import httpx


class ErrorCategory(str, enum.Enum):
    BILLING = "billing"  # the account is out of credit — retrying in minutes cannot help
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"  # bad/revoked key or no permission — retrying repeats it identically
    TRANSIENT = "transient"  # 5xx, timeout, network
    INVALID_REQUEST = "invalid_request"  # any other 4xx — the request itself is wrong
    UNKNOWN = "unknown"  # no status and not a recognisable transport failure


# Substrings (lower-case) of an error's code/type/message that mean "out of credit". Deliberately
# NOT a bare "quota" or "billing": Gemini's per-minute "Quota exceeded for metric ..." 429 is an
# ordinary rate limit, and OpenAI's rate-limit text also mentions the plan. OpenAI's out-of-credit
# error is recognised by its `insufficient_quota` code, Anthropic's by its message. The Gemini /
# xAI / DeepSeek wordings are from provider docs, not yet seen in production — T7 step 6 reviews
# `[unknown]` / `[invalid_request]` rows for any that were missed.
_BILLING_MARKERS = (
    "insufficient_quota",
    "credit balance",
    "insufficient balance",
    "credits are depleted",
    "out of credits",
    "purchase more credits",
)

# Exception classes (matched by name anywhere in the MRO, so no SDK import) that mean the request
# never got an answer: the OpenAI and Anthropic SDKs both name theirs these.
_TRANSPORT_CLASS_NAMES = {"APITimeoutError", "APIConnectionError"}


def _status_of(exc: BaseException) -> int | None:
    """HTTP status from `status_code` (openai, anthropic) or an int `code` (google-genai)."""
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _searchable_text(exc: BaseException) -> str:
    """Lower-cased concatenation of everything that may name the failure: the message, a string
    `code`, and the (possibly nested) response body / details, JSON-dumped.
    """
    parts: list[str] = [str(exc)]
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        parts.append(code)
    for attr in ("message", "status", "body", "details"):
        value = getattr(exc, attr, None)
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        else:
            try:
                parts.append(json.dumps(value, default=str))
            except (TypeError, ValueError):
                parts.append(str(value))
    return " ".join(parts).lower()


def _is_transport_failure(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, httpx.TransportError)):
        return True
    return any(cls.__name__ in _TRANSPORT_CLASS_NAMES for cls in type(exc).__mro__)


def classify_provider_error(exc: BaseException) -> ErrorCategory:
    """Which `ErrorCategory` `exc` (as raised by an adapter's `run`) belongs to.

    Order matters: billing is checked before auth because some providers report an empty balance
    as 403, and a 5xx is never billing whatever its text says.
    """
    status = _status_of(exc)

    if status == 402:
        return ErrorCategory.BILLING
    if status is not None and status >= 500:
        return ErrorCategory.TRANSIENT
    if any(marker in _searchable_text(exc) for marker in _BILLING_MARKERS):
        return ErrorCategory.BILLING
    if status in (401, 403):
        return ErrorCategory.AUTH
    if status == 429:
        return ErrorCategory.RATE_LIMIT
    if status == 408:
        return ErrorCategory.TRANSIENT
    if status is not None and 400 <= status < 500:
        return ErrorCategory.INVALID_REQUEST
    if _is_transport_failure(exc):
        return ErrorCategory.TRANSIENT
    return ErrorCategory.UNKNOWN
