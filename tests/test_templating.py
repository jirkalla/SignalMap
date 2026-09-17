"""render()'s locale-switch `next` target (app/templating.py `_locale_switch_next`).

Manually found, 2026-09-14: base.html's EN/DE links used to build `next` from
`request.url.path` unconditionally. That's correct for a GET page, but several routers render a
template directly from a POST handler (a form re-shown with validation errors, a delete blocked
by a foreign key, the bulk-import preview) — `request.url.path` there is a POST-only route, so
`/set-locale/{locale}`'s GET redirect back to it 405s. Fixed by falling back to the POST
request's own `Referer` header (the GET page the form was submitted from) when the page being
rendered was itself a POST.
"""

from fastapi.testclient import TestClient

from app.models import Prompt


def test_locale_link_uses_request_path_on_a_get_page(authed_client: TestClient):
    response = authed_client.get("/clients")
    assert response.status_code == 200
    assert 'href="/set-locale/en?next=/clients"' in response.text


def test_locale_link_falls_back_to_referer_on_a_post_rendered_page(
    authed_client: TestClient, sample_prompt: Prompt, seed
):
    """Reproduces the exact bug: upload a bulk-import file (POST, rendered directly, no
    redirect) with a `Referer` set to the upload form page — the locale links on the resulting
    preview page must point back at that GET-safe page, not at the POST-only preview URL itself.
    """
    upload_form_url = f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import"
    csv_content = "text,market_code,topic,is_active\nBrand new question for the locale test?,,Topic,true\n".encode(
        "utf-8-sig"
    )

    response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/preview",
        data={"default_market_id": seed["market"].id},
        files={"file": ("prompts.csv", csv_content, "text/csv")},
        headers={"referer": f"http://testserver{upload_form_url}"},
    )
    assert response.status_code == 200
    assert f'href="/set-locale/en?next={upload_form_url}"' in response.text
    assert f'href="/set-locale/de?next={upload_form_url}"' in response.text

    # Following the link must now actually work — a GET redirect target, not a 405.
    redirect_response = authed_client.get(
        f"/set-locale/en?next={upload_form_url}", follow_redirects=False
    )
    assert redirect_response.status_code == 303
    assert redirect_response.headers["location"] == upload_form_url


def test_locale_link_falls_back_to_clients_when_post_rendered_page_has_no_referer(
    authed_client: TestClient, sample_prompt: Prompt, seed
):
    csv_content = "text,market_code,topic,is_active\nAnother brand new question?,,Topic,true\n".encode("utf-8-sig")
    response = authed_client.post(
        f"/prompt-sets/{sample_prompt.prompt_set_id}/prompts/import/preview",
        data={"default_market_id": seed["market"].id},
        files={"file": ("prompts.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200
    assert 'href="/set-locale/en?next=/clients"' in response.text
