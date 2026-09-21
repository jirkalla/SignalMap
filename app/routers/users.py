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
from app.models.schedule import RunSchedule
from app.models.user import ROLES, build_user
from app.services.notifications import notify
from app.templating import get_t, render

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_role("admin"))])

_password_helper = PasswordHelper()
_role_options = [(role, role.capitalize()) for role in ROLES]


def _get_user_or_404(db: Session, request: Request, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError("user_not_found", get_t(request)("errors.user_not_found"), status_code=404)
    return user


def _form_error_response(request: Request, *, title: str, action: str, user: User | None, error: str, status_code: int):
    """Re-render the create/edit form with an inline error — the same shape whether the problem
    is a duplicate email (create only) or an invalid role (create or edit), so both call sites
    share one response builder instead of two near-identical ones.
    """
    return render(
        request,
        "users/form.html",
        {"title": title, "action": action, "cancel_url": "/users", "user": user, "role_options": _role_options, "error": error},
        status_code=status_code,
    )


def _duplicate_email_response(request: Request):
    t = get_t(request)
    return _form_error_response(
        request, title=t("user.create_title"), action="/users", user=None, error=t("errors.user_email_duplicate"), status_code=409
    )


def _pause_schedules_for_deactivated_user(db: Session, user: User) -> None:
    """Pause every currently-active schedule this user owns (docs/TASKS_SCHEDULER.md design
    decision 23) and fire one summary notification — called only from the deactivation branch of
    `toggle_user_active`, never from reactivation, so reactivating a user can never silently
    resume the schedules it paused. `next_run_at` is cleared the same way `toggle_schedule`'s own
    pause branch clears it, so a stale due time can't linger on a schedule nothing will enqueue.
    """
    schedules = db.scalars(
        select(RunSchedule).where(RunSchedule.created_by_user_id == user.id, RunSchedule.is_active.is_(True))
    ).all()
    if not schedules:
        return
    for schedule in schedules:
        schedule.is_active = False
        schedule.inactive_reason = "owner_deactivated"
        schedule.next_run_at = None
    db.commit()
    notify(
        db,
        "schedule.owner_deactivated",
        {
            "deactivated_user_id": user.id,
            "deactivated_user_name": user.name,
            "count": len(schedules),
            "client_names": sorted({schedule.client.name for schedule in schedules}),
        },
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

    `role` is checked against `ROLES` before it ever reaches the database — without this, an
    invalid value hit the `ck_users_role` CHECK constraint instead, which raised the exact same
    `IntegrityError` the duplicate-email fallback below assumes means a duplicate email, silently
    mislabeling the real error (found in code review, 2026-09-12).
    """
    t = get_t(request)
    if role not in ROLES:
        return _form_error_response(
            request, title=t("user.create_title"), action="/users", user=None, error=t("errors.invalid_role"), status_code=400
        )
    email = email.strip().lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        return _duplicate_email_response(request)
    user = build_user(email=email, password=password, name=name.strip(), role=role)
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
    current: User = Depends(current_active_user),
):
    """Update a user's name/role (admin only).

    Refuses to change the logged-in admin's own role (found in code review, 2026-09-12) — same
    self-lockout concern `toggle_user_active` below already guards against for deactivation: the
    sole admin submitting `role=viewer` for themselves would leave the app with zero accounts able
    to reach this admin-only router again. Editing your own name is still allowed; only an actual
    role change on your own row is blocked.

    `role` is checked against `ROLES` BEFORE the self-role-change guard, not after (found in a
    second round of code review, 2026-09-12) — checking self-lockout first meant an admin
    submitting an invalid role for their OWN row got the generic "can't change your own role" 403
    (losing their edited name along the way) instead of the same friendly "not a valid role"
    inline re-render every other invalid-role submission gets. Validating input before applying a
    business rule on top of it is also just the more defensible order in general.
    """
    user = _get_user_or_404(db, request, user_id)
    if role not in ROLES:
        t = get_t(request)
        return _form_error_response(
            request, title=t("user.edit_title"), action=f"/users/{user_id}/edit", user=user, error=t("errors.invalid_role"), status_code=400
        )
    if user.id == current.id and role != user.role:
        raise AppError("forbidden", get_t(request)("errors.user_cannot_change_own_role"), status_code=403)
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

    Also reactivates the account (`is_active = True`) — an admin deliberately setting a specific
    password for someone is a clear signal they want that person to be able to log in again;
    `scripts/create_admin.py --reset-password` already did this and this route didn't, an
    undocumented divergence between two paths both named "reset the password" found in code
    review, 2026-09-12. Deactivation stays a separate, explicit action (`toggle_user_active`
    below) — this only ever reactivates, never deactivates.
    """
    user = _get_user_or_404(db, request, user_id)
    user.hashed_password = _password_helper.hash(password)
    user.must_change_password = True
    user.is_active = True
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

    Deactivating also pauses every schedule this user owns (docs/TASKS_SCHEDULER.md T9, design
    decision 23) — a schedule created by someone no longer with the company must not keep spending
    money unattended. Reactivating the account deliberately does **not** resume them: silently
    turning paid runs back on for a person who left is worse than the pause itself staying silent
    a while longer. Resuming is a separate, conscious admin action on `/schedules`.
    """
    user = _get_user_or_404(db, request, user_id)
    if user.id == current.id and user.is_active:
        raise AppError("forbidden", get_t(request)("errors.user_cannot_deactivate_self"), status_code=403)
    user.is_active = not user.is_active
    if not user.is_active:
        _pause_schedules_for_deactivated_user(db, user)
    db.commit()
    return RedirectResponse(url="/users", status_code=303)
