"""Registry mapping a Provider.code to its adapter class.

Adding a new provider later means adding a new adapter module plus one
entry here — routers never import a specific adapter class directly.
"""

# The openai and anthropic SDKs load these lazily on first `client.chat` /
# `client.responses` / `client.messages` access (cached_property + local import in their
# _client.py). Left lazy, that first import happens inside a request thread, and two
# concurrent runs can deadlock on Python's per-module import lock (_DeadlockError, seen on
# the first Grok run after the v1.1.0 deploy). Importing them here moves it to process
# startup, in the main thread. google-genai doesn't need this: it imports Models eagerly.
import anthropic.resources.messages  # noqa: F401
import openai.resources.chat  # noqa: F401
import openai.resources.responses  # noqa: F401

from app.adapters.anthropic import AnthropicAdapter
from app.adapters.base import ProviderAdapter
from app.adapters.deepseek import DeepSeekAdapter
from app.adapters.google import GoogleGeminiAdapter
from app.adapters.grok import GrokAdapter
from app.adapters.openai import OpenAIAdapter
from app.adapters.perplexity import PerplexityAdapter

ADAPTERS: dict[str, type[ProviderAdapter]] = {
    "google_gemini": GoogleGeminiAdapter,
    "anthropic": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "perplexity": PerplexityAdapter,
    "deepseek": DeepSeekAdapter,
    "xai": GrokAdapter,
}


def get_adapter(provider_code: str) -> ProviderAdapter:
    """Instantiate the adapter registered for `provider_code`.

    Raises KeyError if no adapter is registered — callers should turn that
    into a structured AppError rather than a raw 500.
    """
    return ADAPTERS[provider_code]()


def register_adapter(provider_code: str, adapter: type[ProviderAdapter]) -> None:
    """Register (or override) the adapter class used for `provider_code`.

    The test seam: tests/conftest.py uses this to swap in a FakeAdapter for
    'google_gemini' so app/routers/runs.py never makes a real Gemini call
    during a test run.
    """
    ADAPTERS[provider_code] = adapter


def has_adapter(provider_code: str) -> bool:
    """Whether `provider_code` has a working adapter registered.

    The single place that answers "is this provider runnable" — used by both
    app/routers/prompts.py (to decide which providers show up in the
    run-trigger dropdown) and app/routers/runs.py (to reject a run against a
    provider with no adapter), so the two checks can't drift out of sync.
    """
    return provider_code in ADAPTERS
