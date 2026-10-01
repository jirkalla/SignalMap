"""Every enum value the UI builds a translation key from must have that key, in every locale.

docs/TASKS_CITATION_HARDENING.md T5, design decision 8. Parity BETWEEN locales is already checked
at import (`app/i18n/__init__.py`'s `_load_translations`), but nothing checked that every VALUE of
an enum has a key at all: `run.reason_http_429` and `run.reason_http_other` were valid
`UNVERIFIABLE_REASONS` with no translation, so the run detail page returned a 500 (`t()` raises
`KeyError` on a missing key) the first time a citation hit one (v1.2.1), and `/ops` showed the raw
code instead of a label. Both gaps are exactly what this test now catches before it ships.

Pure Python, no database or HTTP client (same shape as tests/test_version.py).
"""

from app.i18n import SUPPORTED_LOCALES, get_translator
from app.models.verification import HUMAN_VERDICTS, UNVERIFIABLE_REASONS, VERDICTS
from app.services.ops_dashboard import CAPTURE_REASONS
from app.services.verification_display import VERDICT_STYLES


def _missing(keys: list[str]) -> list[str]:
    """`"<locale>: <key>"` for every key that has no translation in some supported locale.

    Goes through the real `t()` the templates call, so "missing" here means exactly what it means
    on a rendered page: the `KeyError` that becomes a 500.
    """
    missing = []
    for locale in SUPPORTED_LOCALES:
        t = get_translator(locale)
        for key in keys:
            try:
                t(key)
            except KeyError:
                missing.append(f"{locale}: {key}")
    return missing


def _reason_keys(reasons: tuple[str, ...]) -> list[str]:
    """The two keys the app builds from a reason value: `partials/verification.html` uses
    `run.reason_<r>`; the `/ops` capture-reason table uses `ops.capture_reason_<r>`.
    """
    return [f"{prefix}{reason}" for reason in reasons for prefix in ("run.reason_", "ops.capture_reason_")]


def _assert_nothing_missing(keys: list[str], what: str) -> None:
    missing = _missing(keys)
    assert not missing, (
        f"{what} without a translation — add these keys to app/i18n/en.json AND de.json "
        f"(a missing key is a KeyError, i.e. a 500 on the page that renders it): {missing}"
    )


def test_every_unverifiable_reason_has_a_translation_for_the_run_page_and_ops():
    _assert_nothing_missing(_reason_keys(UNVERIFIABLE_REASONS), "UNVERIFIABLE_REASONS value(s)")


def test_every_capture_reason_ops_labels_has_a_translation():
    """`CAPTURE_REASONS` = the synthetic buckets (`success`, `not_captured`) + every capture
    failure; the `/ops` page builds its label map from it, so each needs an `ops.capture_reason_`.
    """
    _assert_nothing_missing([f"ops.capture_reason_{reason}" for reason in CAPTURE_REASONS], "CAPTURE_REASONS value(s)")


def test_capture_reasons_cover_every_unverifiable_reason():
    """The `/ops` labels come from `CAPTURE_REASONS`; a reason missing from it would silently fall
    back to the raw code in the table, which is the second half of the gap this test guards.
    """
    assert set(UNVERIFIABLE_REASONS) <= set(CAPTURE_REASONS)


def test_every_verdict_is_styled_and_its_label_is_translated():
    """`VERDICT_STYLES` is what the run page looks a verdict up in — a verdict missing from it just
    vanishes from the filter chips and the highlighting (see its own comment), so it is required
    here, along with the label key each entry points at.
    """
    unstyled = [verdict for verdict in VERDICTS if verdict not in VERDICT_STYLES]
    assert not unstyled, f"VERDICTS without a VERDICT_STYLES entry in app/services/verification_display.py: {unstyled}"
    _assert_nothing_missing([VERDICT_STYLES[verdict].label_key for verdict in VERDICTS], "VERDICTS label key(s)")


def test_verdict_styles_do_not_describe_verdicts_that_do_not_exist():
    assert set(VERDICT_STYLES) <= set(VERDICTS)


def test_every_human_verdict_has_a_translation():
    """Same pattern in the same domain: templates build `verification.human_verdict_<v>` from it."""
    _assert_nothing_missing([f"verification.human_verdict_{verdict}" for verdict in HUMAN_VERDICTS], "HUMAN_VERDICTS value(s)")


def test_a_reason_without_a_translation_is_caught():
    """Test of the test: the check must really fail for a new value nobody translated yet, and name
    exactly the missing keys (both prefixes, every locale) — otherwise it guards nothing.
    """
    missing = _missing(_reason_keys(UNVERIFIABLE_REASONS + ("brand_new_reason",)))

    expected = {
        f"{locale}: {prefix}brand_new_reason"
        for locale in SUPPORTED_LOCALES
        for prefix in ("run.reason_", "ops.capture_reason_")
    }
    assert set(missing) == expected
