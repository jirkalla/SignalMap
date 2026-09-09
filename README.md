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
   start the app):

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

4. Open the app:

   - http://localhost:58000/clients — the app itself
   - http://localhost:58000/help — in-app usage guide (how to fill in
     clients, prompt sets, prompts, markets, and read a run)
   - http://localhost:58000/docs — interactive API reference (Swagger)

Every later `docker compose up -d --build` is enough for subsequent runs;
step 3 only needs re-running after a new migration is added.

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
