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
from app.models import User

__all__ = ["can_edit", "get_t", "render", "templates"]

templates = Jinja2Templates(directory="app/templates")


def can_edit(user: "User | None") -> bool:
    """True for a logged-in admin/editor — the single place every template's "can this role
    mutate data" check reads from, instead of each of ~25 `{% if %}` blocks across 8 templates
    repeating the literal `current_user.role in ('admin', 'editor')` (found in code review,
    2026-09-12; a future role rename or 4th role only needs updating here). This is UX/display
    only, same caveat as everywhere else in this app — the real enforcement is always
    `app.auth.require_role` on the route itself.
    """
    return user is not None and user.role in ("admin", "editor")


templates.env.globals["can_edit"] = can_edit

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
    """Render a Jinja2 template with `request`, `locale`, `t()`, and `current_user` already in context.

    `current_user` is read from `request.state.current_user` when present — the
    `enforce_password_change` middleware (app/main.py) already resolves it via
    `current_user_from_cookie` on every request before routing even starts, so re-decoding the
    JWT and re-querying the database here would be a second, redundant lookup for the exact same
    value on the exact same request (found in code review, 2026-09-12). Falls back to resolving it
    directly for the rare case `render()` is called outside that middleware's request cycle.
    """
    locale = resolve_locale(request.cookies.get(LOCALE_COOKIE_NAME))
    if hasattr(request.state, "current_user"):
        current_user = request.state.current_user
    else:
        current_user = current_user_from_cookie(request)
    ctx: dict = {
        "request": request,
        "t": get_translator(locale),
        "locale": locale,
        "current_user": current_user,
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)
