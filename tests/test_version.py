"""Guard against `app.__version__` and `CHANGELOG.md` drifting apart.

docs/TASKS_VERSIONING.md design decision 14: without a mechanical check, the two are certain to
diverge exactly the way `FastAPI(version="0.1.0")` already had (frozen at the phase-1 value while
the app kept shipping). Both tests are pure text parsing — no database, no HTTP client — so they
don't belong to any fixture in tests/conftest.py; they just happen to run inside the pytest suite
that always has the test database up.
"""

import re
from datetime import date
from pathlib import Path

from app import __version__

CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

# "## [1.2.3] - 2026-09-22" — deliberately not matching "## [Unreleased]", which has no version
# or date. Anchored to the start of the line so a version number mentioned in prose can't match.
_VERSION_HEADING_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", re.MULTILINE)


def _released_versions() -> list[tuple[str, str]]:
    """Every `[version] - date` heading in CHANGELOG.md, in file order (newest first)."""
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    return _VERSION_HEADING_RE.findall(text)


def test_changelog_top_version_matches_app_version():
    """The first released heading (below `[Unreleased]`) must be the version the app reports —
    otherwise a release shipped without updating one of the two, or vice versa.
    """
    versions = _released_versions()
    assert versions, "CHANGELOG.md has no '## [X.Y.Z] - YYYY-MM-DD' heading to check against"
    top_version, _ = versions[0]
    assert top_version == __version__, (
        f"CHANGELOG.md's newest entry is {top_version!r} but app.__version__ is {__version__!r} — "
        "bump whichever one is behind (docs/DEPLOYMENT.md §0)."
    )


def test_changelog_versions_are_descending_with_valid_dates():
    """Released headings must read newest-to-oldest and carry a real calendar date — a changelog

    where entries are out of order or backdated is worse than no changelog, since it actively
    misleads whoever is trying to reconstruct what shipped when.
    """
    versions = _released_versions()
    assert versions, "CHANGELOG.md has no '## [X.Y.Z] - YYYY-MM-DD' heading to check against"

    parsed = []
    for version_str, date_str in versions:
        parsed.append((tuple(int(part) for part in version_str.split(".")), date.fromisoformat(date_str)))

    version_tuples = [v for v, _ in parsed]
    assert version_tuples == sorted(version_tuples, reverse=True), (
        "CHANGELOG.md version headings must be listed newest-first: " f"found {[versions[i][0] for i in range(len(versions))]}"
    )

    dates = [d for _, d in parsed]
    assert dates == sorted(dates, reverse=True), "CHANGELOG.md version headings must be listed newest-first by date too"
