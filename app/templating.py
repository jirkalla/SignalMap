"""Shared Jinja2Templates instance and a render() helper that injects the

active locale's t() translator into every template's context, so routers
never have to wire that up individually.
"""

import json

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.auth import current_user_from_cookie
from app.i18n import LOCALE_COOKIE_NAME, get_t, get_translator, resolve_locale

__all__ = ["get_t", "render", "templates"]

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


def render(request: Request, template_name: str, context: dict | None = None, status_code: int = 200):
    """Render a Jinja2 template with `request`, `locale`, `t()`, and `current_user` already in context."""
    locale = resolve_locale(request.cookies.get(LOCALE_COOKIE_NAME))
    ctx: dict = {
        "request": request,
        "t": get_translator(locale),
        "locale": locale,
        "current_user": current_user_from_cookie(request),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)
