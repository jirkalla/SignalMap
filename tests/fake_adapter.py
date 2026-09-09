"""FakeAdapter — a ProviderAdapter test double that never calls a real provider API.

Configured via class attributes (set them, trigger a run, the fixture in
conftest.py resets them after each test) since ADAPTERS registers a class,
not an instance — app.adapters.get_adapter() always instantiates fresh.
"""

from app.adapters.base import ProviderAdapter, RawResponsePayload


class FakeAdapter:
    """Returns `payload_to_return` on `run()`, or raises `error_to_raise` if set."""

    payload_to_return: RawResponsePayload | None = None
    error_to_raise: Exception | None = None

    def run(
        self, prompt_text: str, model_name: str, *, system_instruction: str | None = None
    ) -> RawResponsePayload:
        if FakeAdapter.error_to_raise is not None:
            raise FakeAdapter.error_to_raise
        assert FakeAdapter.payload_to_return is not None, "Set FakeAdapter.payload_to_return before triggering a run."
        return FakeAdapter.payload_to_return


# Static check that FakeAdapter satisfies the ProviderAdapter protocol.
_conforms: ProviderAdapter = FakeAdapter()
