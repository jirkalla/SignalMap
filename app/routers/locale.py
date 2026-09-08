"""Locale switching — sets the locale cookie and redirects back where the user was."""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.i18n import LOCALE_COOKIE_NAME, SUPPORTED_LOCALES

router = APIRouter(tags=["locale"])


@router.get("/set-locale/{locale}")
def set_locale(locale: str, next: str = "/clients") -> RedirectResponse:
    """Set the UI locale (en/de) via a cookie, then redirect to `next`.

    `next` is only ever followed when it's a same-site relative path
    (starts with a single "/"), to rule out open-redirect abuse.
    """
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/clients"
    response = RedirectResponse(url=safe_next, status_code=303)
    if locale in SUPPORTED_LOCALES:
        response.set_cookie(LOCALE_COOKIE_NAME, locale, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response
