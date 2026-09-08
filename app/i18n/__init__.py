"""Minimal DE/EN translation mechanism (mechanism only — see docs/REQUIREMENTS.md NFR-2).

Single JSON source of truth per locale (en.json / de.json), loaded once at
import time and shared as the same files a future Vue3 island would import
into vue-i18n. Locale is chosen via a cookie (a `?lang=` query param sets
it), not URL path prefixing — the signalmap-conventions skill recommends
path-prefix routing (`/de/...`) as the long-term direction, but that's a
bigger architectural change deliberately out of scope for phase 1's exact
task list (flagged, not silently decided either way).

A missing key raises loudly (KeyError) rather than silently rendering the
key name, and the two locale files' key sets are checked for parity at
import time.
"""

import json
from pathlib import Path
from typing import Callable

_I18N_DIR = Path(__file__).parent
SUPPORTED_LOCALES = ("en", "de")
DEFAULT_LOCALE = "en"
LOCALE_COOKIE_NAME = "signalmap_locale"


def _load_translations() -> dict[str, dict[str, str]]:
    translations: dict[str, dict[str, str]] = {}
    for locale in SUPPORTED_LOCALES:
        with (_I18N_DIR / f"{locale}.json").open(encoding="utf-8") as f:
            translations[locale] = json.load(f)

    key_sets = {locale: set(strings) for locale, strings in translations.items()}
    reference_locale, reference_keys = next(iter(key_sets.items()))
    for locale, keys in key_sets.items():
        if keys != reference_keys:
            missing = reference_keys - keys
            extra = keys - reference_keys
            raise RuntimeError(
                f"i18n key sets out of sync between '{reference_locale}' and '{locale}': "
                f"missing={sorted(missing)} extra={sorted(extra)}"
            )
    return translations


_TRANSLATIONS = _load_translations()


def get_translator(locale: str) -> Callable[[str], str]:
    """Return a t(key) callable bound to `locale`, falling back to DEFAULT_LOCALE."""
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    strings = _TRANSLATIONS[locale]

    def t(key: str) -> str:
        if key not in strings:
            raise KeyError(f"Missing i18n key '{key}' for locale '{locale}'")
        return strings[key]

    return t


def resolve_locale(cookie_value: str | None) -> str:
    """Resolve the active locale from the locale cookie value, defaulting to English."""
    if cookie_value in SUPPORTED_LOCALES:
        return cookie_value
    return DEFAULT_LOCALE
