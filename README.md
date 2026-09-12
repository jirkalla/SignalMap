# SignalMap

An internal AI corporate-perception intelligence tool: it runs configured
prompts against AI providers, stores the raw answers and citations, and
(in later phases) analyzes them. This is the phase 1 vertical slice —
see [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) and
[docs/TASKS.md](docs/TASKS.md) for scope, and
[AI_INSTRUCTIONS.md](AI_INSTRUCTIONS.md) for how work on this repo is done.

## Run it

1. Copy the env template and fill in your Google API key
   (https://aistudio.google.com/apikey — required to trigger a run, not to
   start the app) and a `SECRET_KEY` (required to start the app at all — it
   signs the login session cookie; generate one with
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`):

   ```bash
   cp .env.example .env
   ```

2. Start Postgres and the app:

   ```bash
   docker compose up -d --build
   ```

3. Create the schema and seed data (markets, the Google provider, the two
   Gemini models) — the only manual setup step:

   ```bash
   docker compose exec app alembic upgrade head
   ```

4. Create the first admin account — there's no other way in, since the
   in-app user management screen (`/users`) is itself admin-only:

   ```bash
   docker compose exec app python -m scripts.create_admin --email you@example.com --name "Your Name"
   ```

   Prompts for a password interactively (never pass it as a CLI argument).
   The account must change that password on first login.

5. Open the app:

   - http://localhost:58000/login — log in with the account from step 4
   - http://localhost:58000/clients — the app itself
   - http://localhost:58000/help — in-app usage guide (how to fill in
     clients, prompt sets, prompts, markets, and read a run)
   - http://localhost:58000/docs — interactive API reference (Swagger)

Every later `docker compose up -d --build` is enough for subsequent runs;
step 3 only needs re-running after a new migration is added, and step 4 only
once per environment (further accounts are created through `/users` once an
admin exists).

## Stop it

```bash
docker compose stop
```

Stops the containers but keeps them (and the database volume) around — the
fastest way back in is plain `docker compose up -d` next time, no rebuild
or migration needed.

To remove the containers too (data survives, in the `pgdata` volume):

```bash
docker compose down
```

To wipe the database as well — next start needs `alembic upgrade head`
again:

```bash
docker compose down -v
```

## Running tests

Tests run against a second, real Postgres database on the same compose
container (`signalmap_test`) — not SQLite, since the app relies on
JSONB/GIN indexes SQLite can't faithfully emulate. They never call the
real Gemini API (see `tests/fake_adapter.py`).

1. Create the test database once (inside the running `postgres` container):

   ```bash
   docker compose exec postgres psql -U signalmap_user -d signalmap -c "CREATE DATABASE signalmap_test;"
   ```

2. Install the dev dependencies and run the suite (inside the running `app` container):

   ```bash
   docker compose exec app pip install -r requirements-dev.txt
   docker compose exec app python -m pytest
   ```

   Use `python -m pytest`, not a bare `pytest` — the app container runs as
   a non-root user (HD-T2), so `pip install` falls back to a user-site
   install (`~/.local/bin`), which isn't on `$PATH`. `python -m pytest`
   finds it regardless.

Each test run creates its own tables via SQLAlchemy metadata (not
Alembic) and truncates them between tests — the test database is safe to
throw away and recreate at any time.

## Stack

FastAPI + SQLAlchemy + PostgreSQL, Jinja2 + HTMX (Vue3 islands planned for
the dashboard phase), Tailwind via CDN, Alembic migrations, Docker Compose
for local dev and deployment alike.

## Notes

- The app container runs as a non-root user (`appuser`, uid 1000). Because
  `docker-compose.yaml` bind-mounts the repo over `/code` at runtime, the
  Dockerfile's `chown` only applies to the built image, not the live
  bind-mounted view — this hasn't caused any issue so far (the app only
  reads from `/code`, never writes to it), but worth revisiting if a
  future VPS deployment adds any runtime write into the mounted source
  tree.
