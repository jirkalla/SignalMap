"""Page footer (app/templates/partials/footer.html) — docs/TASKS_VERSIONING.md VER-T1/VER-T2.

Decision 10: the exact commit SHA and the environment must never reach an anonymous visitor —
an exact build on the public login page lets it be matched against a known vulnerability, the
same reason app/main.py disables the unauthenticated /docs and /openapi.json. The version number
itself is fine for everyone. `build_info()` (app/templating.py) is a template global read at
render time, not a snapshot, so each test patches that callable rather than Settings itself.
"""

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.templating import templates

FAKE_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def baked_build(monkeypatch):
    """Simulate an image built with GIT_SHA/BUILD_TIME in a non-production environment."""
    monkeypatch.setitem(
        templates.env.globals,
        "build_info",
        lambda: {"git_sha": FAKE_SHA, "build_time": "2026-09-23T06:00:00Z", "environment": "development"},
    )


def test_anonymous_footer_shows_version_but_no_sha_or_environment(client: TestClient, baked_build):
    response = client.get("/login")
    assert response.status_code == 200
    assert f"v{__version__}" in response.text
    # Covers the title tooltip too, not just the visible short SHA.
    assert FAKE_SHA[:7] not in response.text
    assert "2026-09-23T06:00:00Z" not in response.text
    assert ">development<" not in response.text


def test_logged_in_footer_shows_short_sha_tooltip_and_environment(authed_client: TestClient, baked_build):
    response = authed_client.get("/clients")
    assert response.status_code == 200
    assert f"v{__version__} · {FAKE_SHA[:7]}" in response.text
    assert f'title="{FAKE_SHA} · 2026-09-23T06:00:00Z"' in response.text
    assert ">development<" in response.text


def test_logged_in_footer_without_build_args_omits_sha(authed_client: TestClient, monkeypatch):
    """A plain dev build passes no build args — the footer must degrade to just the version."""
    monkeypatch.setitem(
        templates.env.globals,
        "build_info",
        lambda: {"git_sha": "", "build_time": "", "environment": "development"},
    )
    response = authed_client.get("/clients")
    assert response.status_code == 200
    assert f">v{__version__}</span>" in response.text


def test_production_hides_environment_badge(authed_client: TestClient, baked_build, monkeypatch):
    monkeypatch.setitem(
        templates.env.globals,
        "build_info",
        lambda: {"git_sha": FAKE_SHA, "build_time": "2026-09-23T06:00:00Z", "environment": "production"},
    )
    response = authed_client.get("/clients")
    assert ">production<" not in response.text
