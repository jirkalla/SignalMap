"""User management: admin-only CRUD (docs/TASKS_PHASE6.md P6-T4).

Password hashing here goes through fastapi-users' PasswordHelper directly, not the full async
UserManager.create() flow — hashing itself is synchronous CPU work, so there's no reason to
route account creation through the async session (app/database.py) that exists only for
fastapi-users' own login/logout routes (app/auth.py). Every route below uses the same
synchronous session as the rest of the app.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi_users.password import PasswordHelper
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import current_active_user, require_role
from app.database import get_db
from app.errors import AppError
from app.models import User
from app.models.user import ROLES
from app.templating import get_t, render

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_role("admin"))])

_password_helper = PasswordHelper()
_role_options = [(role, role.capitalize()) for role in ROLES]


def _get_user_or_404(db: Session, request: Request, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError("user_not_found", get_t(request)("errors.user_not_found"), status_code=404)
    return user


def _duplicate_email_response(request: Request):
    return render(
        request,
        "users/form.html",
        {
            "title": get_t(request)("user.create_title"),
            "action": "/users",
            "cancel_url": "/users",
            "user": None,
            "role_options": _role_options,
            "error": get_t(request)("errors.user_email_duplicate"),
        },
        status_code=409,
    )


@router.get("")
def list_users(request: Request, db: Session = Depends(get_db)):
    """List every user account (admin only)."""
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return render(request, "users/list.html", {"users": users})


@router.get("/new")
def new_user_form(request: Request):
    """Render the empty user-creation form (admin only)."""
    t = get_t(request)
    return render(
        request,
        "users/form.html",
        {
            "title": t("user.create_title"),
            "action": "/users",
            "cancel_url": "/users",
            "user": None,
            "role_options": _role_options,
        },
    )


@router.post("")
def create_user(
    request: Request,
    name: str = Form(..., description="The new user's display name."),
    email: str = Form(..., description="Login email — must be unique."),
    role: str = Form(..., description="One of admin/editor/viewer."),
    password: str = Form(..., min_length=8, description="Initial password — the account must change it on first login."),
    db: Session = Depends(get_db),
):
    """Create a new user account (admin only).

    The new account gets `must_change_password=True` — whatever password the admin sets here is
    meant to be used exactly once, communicated to the user out of band (docs/TASKS_PHASE6.md
    design decision 3), not shown/emailed by the app itself. Duplicate email is a
    case-insensitive pre-check (fast path) plus a DB-level unique-index fallback (the actual
    source of truth for a concurrent race), same two-layer pattern as create_client_alias.
    """
    email = email.strip().lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        return _duplicate_email_response(request)
    user = User(
        email=email,
        hashed_password=_password_helper.hash(password),
        name=name.strip(),
        role=role,
        must_change_password=True,
        is_active=True,
        is_verified=False,
        is_superuser=False,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _duplicate_email_response(request)
    return RedirectResponse(url="/users", status_code=303)


@router.get("/{user_id}/edit")
def edit_user_form(request: Request, user_id: int, db: Session = Depends(get_db)):
    """Render the edit form, pre-filled with the account's current name/role.

    Email is immutable here (it's the login identifier) and password changes go through
    `reset_password` below, not this form — so neither field is present.
    """
    user = _get_user_or_404(db, request, user_id)
    t = get_t(request)
    return render(
        request,
        "users/form.html",
        {
            "title": t("user.edit_title"),
            "action": f"/users/{user_id}/edit",
            "cancel_url": "/users",
            "user": user,
            "role_options": _role_options,
        },
    )


@router.post("/{user_id}/edit")
def update_user(
    request: Request,
    user_id: int,
    name: str = Form(..., description="The user's display name."),
    role: str = Form(..., description="One of admin/editor/viewer."),
    db: Session = Depends(get_db),
):
    """Update a user's name/role (admin only)."""
    user = _get_user_or_404(db, request, user_id)
    user.name = name.strip()
    user.role = role
    db.commit()
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/reset-password")
def reset_password(
    request: Request,
    user_id: int,
    password: str = Form(..., min_length=8, description="New password — the account must change it on next login."),
    db: Session = Depends(get_db),
):
    """Set a new password directly (admin only) — there's no emailed reset-link flow to use
    instead (docs/TASKS_PHASE6.md design decision 2). Forces `must_change_password` again, same
    as account creation, so this password is also meant to be used exactly once.
    """
    user = _get_user_or_404(db, request, user_id)
    user.hashed_password = _password_helper.hash(password)
    user.must_change_password = True
    db.commit()
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/toggle-active")
def toggle_user_active(
    request: Request, user_id: int, db: Session = Depends(get_db), current: User = Depends(current_active_user)
):
    """Flip a user account's `is_active` flag (admin only). Never DELETE — `Run.triggered_by_user_id`
    may reference this account, and deleting it would break that audit trail. Same reversible
    toggle pattern as app/routers/ai_models.py's toggle_ai_model_active — deactivating a user
    only blocks login, it never touches their past runs.

    An admin deactivating their own account would lock themselves out with no other admin able
    to undo it via this same admin-only UI (docs/TASKS_PHASE6.md follow-up, 2026-09-12) — refused
    for the logged-in user's own id, same as the reactivation direction being harmless either way.
    """
    user = _get_user_or_404(db, request, user_id)
    if user.id == current.id and user.is_active:
        raise AppError("forbidden", get_t(request)("errors.user_cannot_deactivate_self"), status_code=403)
    user.is_active = not user.is_active
    db.commit()
    return RedirectResponse(url="/users", status_code=303)
