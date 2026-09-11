"""Shared Jinja2Templates instance and a render() helper that injects the

active locale's t() translator into every template's context, so routers
never have to wire that up individually.
"""

import json

import jwt as pyjwt
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi_users.jwt import decode_jwt
from markupsafe import Markup

from app.auth import JWT_AUDIENCE, SESSION_COOKIE_NAME
from app.config import get_settings
from app.database import SessionLocal
from app.i18n import LOCALE_COOKIE_NAME, get_translator, resolve_locale
from app.models import User

templates = Jinja2Templates(directory="app/templates")

# The two Unicode line-terminator characters a JSON string may legally contain but a raw JS
# string literal may not — see _tojson_filter's docstring. Built via chr() rather than typed as
# literal characters in source: both render as an indistinguishable blank in most editors/diffs,
# so a literal one risks silently being "cleaned up" to an ordinary space by a future edit.
_LINE_SEPARATOR = chr(0x2028)
_PARAGRAPH_SEPARATOR = chr(0x2029)


def _tojson_filter(value: object) -> Markup:
    """Serialize a Python value as JSON safe to embed inside a <script> tag.

    FastAPI's Jinja2Templates wraps plain Jinja2, which has no built-in
    `tojson` (that's a Flask-only addition) — registered here so any
    template can do `{{ value | tojson }}` the same way. Escapes '<', '>',
    '&' as \\uXXXX so a string value containing e.g. "</script>" can't break
    out of the tag, plus U+2028/U+2029 (legal in a JSON string, but illegal
    unescaped in a raw JS string literal) so this stays safe even if a future
    template interpolates the result directly into JS source rather than
    into a `type="application/json"` island parsed via JSON.parse. Returns
    Markup so Jinja's autoescaping doesn't re-escape the quotes around it.
    """
    escaped = (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(_LINE_SEPARATOR, "\\u2028")
        .replace(_PARAGRAPH_SEPARATOR, "\\u2029")
    )
    return Markup(escaped)


templates.env.filters["tojson"] = _tojson_filter


def _current_user_from_cookie(request: Request) -> User | None:
    """Best-effort lookup of the logged-in user, for display only (name + logout link in the
    header) — never for authorization, which always goes through `app.auth.current_active_user`/
    `require_role` as a real FastAPI dependency. Decodes the session cookie synchronously via
    fastapi-users' own `decode_jwt` helper (a plain function, not the async `JWTStrategy.
    read_token`), so `render()` — a plain sync function called directly from route bodies, not
    dependency-injected — doesn't need to bridge into an async call for something this low-stakes.
    Returns None on any missing/invalid/expired token, same as an anonymous request.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_jwt(token, get_settings().secret_key, JWT_AUDIENCE)
    except pyjwt.PyJWTError:
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    with SessionLocal() as db:
        return db.get(User, int(user_id))


def get_t(request: Request):
    """Return the t() translator for the request's active locale.

    Use this in route code that needs a translated string outside of a
    template render (e.g. for an AppError message).
    """
    return get_translator(resolve_locale(request.cookies.get(LOCALE_COOKIE_NAME)))


def render(request: Request, template_name: str, context: dict | None = None, status_code: int = 200):
    """Render a Jinja2 template with `request`, `locale`, `t()`, and `current_user` already in context."""
    locale = resolve_locale(request.cookies.get(LOCALE_COOKIE_NAME))
    ctx: dict = {
        "request": request,
        "t": get_translator(locale),
        "locale": locale,
        "current_user": _current_user_from_cookie(request),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)
