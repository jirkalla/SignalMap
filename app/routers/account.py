"""Self-service account settings: the logged-in user's own display name (any role).

Deliberately separate from app/routers/users.py (admin-only CRUD over *other* accounts) — this
router only ever acts on the caller's own row, so it needs no `require_role` beyond being logged
in at all. Also separate from /change-password (app/main.py) — that page is a mandatory security
step shown before a fresh/reset account can go anywhere else in the app, while this is an
optional profile tweak, so mixing the two would force a display-name decision onto someone who
just wants to get past a forced password reset.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import current_active_user
from app.database import get_db
from app.models import User
from app.templating import get_t, render

router = APIRouter(prefix="/account", tags=["account"], dependencies=[Depends(current_active_user)])

_DISPLAY_NAME_MAX_LENGTH = 50


@router.get("")
def account_form(request: Request, current: User = Depends(current_active_user), db: Session = Depends(get_db)):
    """Render the account-settings form, pre-filled with the caller's own current display name."""
    user = db.get(User, current.id)
    return render(request, "account/form.html", {"user": user})


@router.post("")
def update_account(
    request: Request,
    display_name: str = Form(
        "", description="Optional short name shown in the header instead of your full name. Leave empty to show initials."
    ),
    current: User = Depends(current_active_user),
    db: Session = Depends(get_db),
):
    """Set the caller's own display name. No length restriction beyond a generous cap, and no
    character allowlist — real names routinely include diacritics, hyphens, and apostrophes, and
    Jinja2's autoescaping (not input filtering) is what actually guards against XSS here, same as
    `name` elsewhere in this app.
    """
    user = db.get(User, current.id)
    display_name = display_name.strip()
    if len(display_name) > _DISPLAY_NAME_MAX_LENGTH:
        t = get_t(request)
        return render(
            request,
            "account/form.html",
            {"user": user, "error": t("errors.display_name_too_long"), "display_name_input": display_name},
            status_code=400,
        )
    user.display_name = display_name or None
    db.commit()
    return RedirectResponse(url="/account", status_code=303)
