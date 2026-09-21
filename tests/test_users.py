"""Admin-only user management CRUD, and the forced-password-change flow it triggers
(docs/TASKS_PHASE6.md P6-T8).
"""

from datetime import time

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import Prompt, User
from app.models.notification import NotificationOutbox
from app.models.schedule import RunSchedule


def _make_schedule(
    db_session: Session, *, prompt: Prompt, model_ids: list[int], persona_ids: list[int], created_by: User, **overrides
) -> RunSchedule:
    """Minimal active daily schedule owned by `created_by`, for docs/TASKS_SCHEDULER.md T9's
    deactivation-pauses-schedules tests — same shape as tests/test_schedules.py's own helper,
    duplicated locally rather than imported since this file doesn't otherwise touch scheduling.
    """
    defaults = dict(
        client_id=prompt.prompt_set.client_id,
        target_type="prompt",
        target_id=prompt.id,
        model_ids=model_ids,
        market_id=None,
        persona_ids=persona_ids,
        frequency="daily",
        time_of_day=time(6, 0),
        timezone="Europe/Prague",
        max_occurrences=30,
        priority=100,
        is_active=True,
        created_by_user_id=created_by.id,
    )
    defaults.update(overrides)
    schedule = RunSchedule(**defaults)
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)
    return schedule


def test_admin_can_create_list_and_edit_a_user(admin_client: TestClient, db_session: Session):
    create_response = admin_client.post(
        "/users",
        data={"name": "New Person", "email": "new-person@test.local", "role": "editor", "password": "InitialPass123!"},
        follow_redirects=False,
    )
    assert create_response.status_code == 303

    list_response = admin_client.get("/users")
    assert list_response.status_code == 200
    assert "New Person" in list_response.text

    user = db_session.query(User).filter_by(email="new-person@test.local").one()
    assert user.role == "editor"
    assert user.must_change_password is True  # a freshly admin-created account must change it (P6-T5)

    edit_response = admin_client.post(
        f"/users/{user.id}/edit", data={"name": "Renamed Person", "role": "viewer"}, follow_redirects=False
    )
    assert edit_response.status_code == 303
    db_session.refresh(user)
    assert user.name == "Renamed Person"
    assert user.role == "viewer"


def test_duplicate_email_returns_structured_conflict_not_500(admin_client: TestClient, editor_user: User):
    response = admin_client.post(
        "/users",
        data={"name": "Duplicate", "email": editor_user.email, "role": "viewer", "password": "SomePass123!"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "already exists" in response.text


def test_admin_cannot_change_their_own_role(admin_client: TestClient, admin_user: User, db_session: Session):
    """Code-review regression guard, 2026-09-12 — the sole admin submitting a role change for
    their own account must never succeed, or the app could end up with zero accounts able to
    reach this admin-only router again. Editing your own name is still allowed (not asserted here,
    covered implicitly by the role staying the request's only difference from a normal edit).
    """
    response = admin_client.post(
        f"/users/{admin_user.id}/edit", data={"name": admin_user.name, "role": "viewer"}, follow_redirects=False
    )

    assert response.status_code == 403
    db_session.refresh(admin_user)
    assert admin_user.role == "admin"


def test_invalid_role_is_rejected_on_create_not_a_500(admin_client: TestClient):
    """Code-review regression guard, 2026-09-12 — role used to reach the DB unchecked, so a bad
    value 500'd via the CHECK constraint's IntegrityError instead of a graceful 400.
    """
    response = admin_client.post(
        "/users",
        data={"name": "Bad Role", "email": "bad-role@test.local", "role": "superadmin", "password": "SomePass123!"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "valid role" in response.text


def test_invalid_role_is_rejected_on_edit_not_a_500(admin_client: TestClient, editor_user: User):
    response = admin_client.post(
        f"/users/{editor_user.id}/edit", data={"name": editor_user.name, "role": "superadmin"}, follow_redirects=False
    )

    assert response.status_code == 400
    assert "valid role" in response.text


def test_reset_password_reactivates_a_deactivated_account(admin_client: TestClient, editor_user: User, db_session: Session):
    """Code-review regression guard, 2026-09-12 — this route used to leave a deactivated account
    deactivated after a password reset, diverging from scripts/create_admin.py --reset-password
    (which always reactivates) with no documented reason for the difference.
    """
    editor_user.is_active = False
    db_session.commit()

    response = admin_client.post(
        f"/users/{editor_user.id}/reset-password", data={"password": "BrandNewPass456!"}, follow_redirects=False
    )

    assert response.status_code == 303
    db_session.refresh(editor_user)
    assert editor_user.is_active is True


def test_editor_gets_403_on_user_management(authed_client: TestClient):
    response = authed_client.get("/users")

    assert response.status_code == 403


def test_viewer_gets_403_on_user_management(viewer_client: TestClient):
    response = viewer_client.get("/users")

    assert response.status_code == 403


def test_must_change_password_flow_forces_redirect_then_clears_after_change(admin_client: TestClient):
    """A fresh admin-created account is redirected to /change-password on every request until it
    actually changes its password (P6-T5) — regardless of role or where it was headed.

    Uses a second, independent TestClient (not the `client`/`authed_client` fixtures) because
    `admin_client` is already logged in as the admin on this test's one shared cookie jar; the
    fresh account needs its own session to prove the redirect applies to it specifically, not to
    whoever happens to be logged in. It reuses the same dependency_overrides already installed on
    the shared `app` object by the `admin_client` fixture chain, so it still talks to the test DB.
    """
    create_response = admin_client.post(
        "/users",
        data={"name": "Fresh Account", "email": "fresh@test.local", "role": "viewer", "password": "FirstLogin123!"},
        follow_redirects=False,
    )
    assert create_response.status_code == 303

    fresh_client = TestClient(app)
    login_response = fresh_client.post("/auth/login", data={"username": "fresh@test.local", "password": "FirstLogin123!"})
    assert login_response.status_code == 204

    redirected = fresh_client.get("/clients", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/change-password"

    change_response = fresh_client.post(
        "/change-password",
        data={"current_password": "FirstLogin123!", "new_password": "BrandNewPass456!"},
        follow_redirects=False,
    )
    assert change_response.status_code == 303

    after_change = fresh_client.get("/clients", follow_redirects=False)
    assert after_change.status_code == 200


def test_deactivating_user_pauses_their_schedules_and_notifies_once(
    admin_client: TestClient, editor_user: User, sample_prompt: Prompt, seed: dict, db_session: Session
):
    """docs/TASKS_SCHEDULER.md T9, design decision 23 — deactivating a user pauses every active

    schedule they own and fires exactly one summary notification (not one per schedule), same
    "one row for the whole batch" shape as schedule.window_skipped (T8).
    """
    first = _make_schedule(
        db_session, prompt=sample_prompt, model_ids=[seed["model"].id], persona_ids=[seed["persona"].id], created_by=editor_user
    )
    second = _make_schedule(
        db_session,
        prompt=sample_prompt,
        model_ids=[seed["anthropic_model"].id],
        persona_ids=[seed["persona"].id],
        created_by=editor_user,
    )

    response = admin_client.post(f"/users/{editor_user.id}/toggle-active", follow_redirects=False)
    assert response.status_code == 303

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.is_active is False
    assert first.inactive_reason == "owner_deactivated"
    assert first.next_run_at is None
    assert second.is_active is False
    assert second.inactive_reason == "owner_deactivated"

    notifications = db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "schedule.owner_deactivated")).all()
    assert len(notifications) == 1
    assert notifications[0].payload["deactivated_user_id"] == editor_user.id
    assert notifications[0].payload["count"] == 2


def test_deactivating_user_does_not_touch_other_users_schedules(
    admin_client: TestClient, editor_user: User, admin_user: User, sample_prompt: Prompt, seed: dict, db_session: Session
):
    """A deactivation must only ever pause the schedules the deactivated account itself owns —

    an admin's own schedule must survive another user's deactivation untouched.
    """
    other = _make_schedule(
        db_session, prompt=sample_prompt, model_ids=[seed["model"].id], persona_ids=[seed["persona"].id], created_by=admin_user
    )

    response = admin_client.post(f"/users/{editor_user.id}/toggle-active", follow_redirects=False)
    assert response.status_code == 303

    db_session.refresh(other)
    assert other.is_active is True
    assert other.inactive_reason is None


def test_deactivating_user_with_no_active_schedules_does_not_notify(admin_client: TestClient, editor_user: User, db_session: Session):
    response = admin_client.post(f"/users/{editor_user.id}/toggle-active", follow_redirects=False)
    assert response.status_code == 303

    assert db_session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "schedule.owner_deactivated")).all() == []


def test_reactivating_user_does_not_resume_their_paused_schedules(
    admin_client: TestClient, editor_user: User, sample_prompt: Prompt, seed: dict, db_session: Session
):
    """decision 23's core guarantee: reactivating the account is never enough, on its own, to

    silently start paid runs again for someone no longer with the company.
    """
    schedule = _make_schedule(
        db_session, prompt=sample_prompt, model_ids=[seed["model"].id], persona_ids=[seed["persona"].id], created_by=editor_user
    )
    deactivate_response = admin_client.post(f"/users/{editor_user.id}/toggle-active", follow_redirects=False)
    assert deactivate_response.status_code == 303
    db_session.refresh(schedule)
    assert schedule.is_active is False

    reactivate_response = admin_client.post(f"/users/{editor_user.id}/toggle-active", follow_redirects=False)
    assert reactivate_response.status_code == 303
    db_session.refresh(editor_user)
    assert editor_user.is_active is True

    db_session.refresh(schedule)
    assert schedule.is_active is False
    assert schedule.inactive_reason == "owner_deactivated"
