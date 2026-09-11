"""Shared Jinja2Templates instance and a render() helper that injects the

active locale's t() translator into every template's context, so routers
never have to wire that up individually.
"""

import json

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.i18n import LOCALE_COOKIE_NAME, get_translator, resolve_locale

templates = Jinja2Templates(directory="app/templates")


def _tojson_filter(value: object) -> Markup:
    """Serialize a Python value as JSON safe to embed inside a <script> tag.

    FastAPI's Jinja2Templates wraps plain Jinja2, which has no built-in
    `tojson` (that's a Flask-only addition) — registered here so any
    template can do `{{ value | tojson }}` the same way. Escapes '<', '>',
    '&' as \\uXXXX so a string value containing e.g. "</script>" can't break
    out of the tag, and returns Markup so Jinja's autoescaping doesn't
    re-escape the quotes around the result.
    """
    return Markup(json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


templates.env.filters["tojson"] = _tojson_filter


def get_t(request: Request):
    """Return the t() translator for the request's active locale.

    Use this in route code that needs a translated string outside of a
    template render (e.g. for an AppError message).
    """
    return get_translator(resolve_locale(request.cookies.get(LOCALE_COOKIE_NAME)))


def render(request: Request, template_name: str, context: dict | None = None, status_code: int = 200):
    """Render a Jinja2 template with `request`, `locale`, and `t()` already in context."""
    locale = resolve_locale(request.cookies.get(LOCALE_COOKIE_NAME))
    ctx: dict = {"request": request, "t": get_translator(locale), "locale": locale}
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)
