# SignalMap

An internal AI corporate-perception intelligence tool: it runs configured
prompts against AI providers, stores the raw answers and citations, and
(in later phases) analyzes them. This is the phase 1 vertical slice —
see [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) and
[docs/TASKS.md](docs/TASKS.md) for scope, and
[AI_INSTRUCTIONS.md](AI_INSTRUCTIONS.md) for how work on this repo is done.

## Run locally

Requires a running Docker Engine and Docker Compose **2.23.1 or newer**
(Docker Desktop includes both). Run every command from this repository.
There is one Compose file for local use and Hetzner: Postgres, the app,
the scheduler worker, and Caddy. No external database, image registry, or
frontend build needed.

1. Create your private settings:

   ```bash
   cp .env.example .env
   chmod 600 .env
   openssl rand -hex 32
   openssl rand -hex 32
   ```

   Paste the two different generated values into `POSTGRES_PASSWORD` and
   `SECRET_KEY` in `.env`. Use a hex database password: Compose constructs
   `DATABASE_URL` from it, so you don't maintain the same secret twice.
   Leave the other defaults for local use. Existing installations should
   retain their actual database password and signing key.

2. Build and start everything:

   ```bash
   docker compose up -d --build --wait
   ```

   Postgres becomes healthy first; the app then applies all Alembic
   migrations and starts one Uvicorn process; the scheduler worker and
   Caddy both start once the app is healthy, so the worker never races the
   app over the migration lock. Failed migrations block app startup. There
   is no reload watcher or source bind mount, so code changes require a
   rebuild.

3. Create the first admin (once per database):

   ```bash
   docker compose exec app python -m scripts.create_admin --email you@example.com --name "Your Name"
   ```

   Enter a password at the private prompt. On first login the app asks
   you to change it. Additional users can then be created at `/users`.
   For local convenience only, you can instead set `DEV_ADMIN_EMAIL`,
   `DEV_ADMIN_NAME`, and `DEV_ADMIN_PASSWORD` in `.env`, re-run step 2,
   and run `docker compose exec app python -m scripts.create_admin --from-env`.
   This bootstrap command is safe to repeat.

4. Open [SignalMap](http://localhost:58000/login).
   `/clients` is the app, `/help` is its usage guide, and `/docs` is the
   admin-only API reference. Health: `http://localhost:58000/health`.

Google and Anthropic API keys are optional for startup and login. Add the
relevant key in `.env` to execute real provider runs, then run
`docker compose up -d --wait` to apply the changed environment.

Local traffic is HTTP, bound to `127.0.0.1` only. Only Caddy publishes
ports; the app and Postgres are accessible solely on the Compose network.
All services restart unless explicitly stopped and rotate their logs.
Postgres data and Caddy certificates live in named Docker volumes.

## Move to a small Hetzner server

For a small internal team, start with a **2 vCPU / 4 GB RAM** shared CPU
server with about **40 GB disk**, running Ubuntu 24.04 LTS. This is a
starting estimate, not a load-tested capacity guarantee; AI model calls
run at the providers, not on this machine. Pick the cheapest available
matching plan in [Hetzner Cloud](https://www.hetzner.com/cloud/cost-optimized/).
No server is created by these files.

1. Create the server with your SSH key. Install Docker Engine and the
   Compose plugin using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
   Use a current Compose version (2.23.1+ supports the inline Caddy config).
   Run `sudo systemctl enable --now docker` so it starts after a reboot.
2. Point a domain's DNS A record at the server's IPv4 address. Only add
   an AAAA record if IPv6 is configured and reachable. In the Hetzner
   firewall, allow TCP 22 from your own IP and TCP 80/443 publicly.
   Do not open 5432 or 8000.
3. Copy a checkout containing this deployment setup. From the repository
   on your workstation, replacing `SERVER_IP`:

   ```bash
   ssh root@SERVER_IP 'mkdir -p /opt/signalmap'
   rsync -az --exclude='.git' --exclude='.env' --exclude='.venv' --exclude='__pycache__' --exclude='*.dump' --exclude='docker-compose.override.yaml' ./ root@SERVER_IP:/opt/signalmap/
   ```

4. On the server, `cd /opt/signalmap`, create `.env` as in local step 1
   with new secrets, and change these values:

   ```dotenv
   SITE_ADDRESS=expressyourself.ai
   BIND_ADDRESS=0.0.0.0
   HTTP_PORT=80
   HTTPS_PORT=443
   COOKIE_SECURE=true
   ENVIRONMENT=production
   ```

   The intended production domain is `expressyourself.ai`. Leave `DEV_ADMIN_*` empty.
   Add provider keys if needed. Then use the **same command**:

   ```bash
   docker compose up -d --build --wait
   docker compose exec app python -m scripts.create_admin --email you@example.com --name "Your Name"
   ```

   Caddy obtains and renews HTTPS certificates automatically and redirects
   HTTP to HTTPS when public DNS and ports 80/443 are working. Check
   `https://expressyourself.ai/health` and log in at `https://expressyourself.ai/login`.
   The Compose file does not change between machines.

To start privately before you have a domain, retain the local port and
HTTP settings, set only `ENVIRONMENT=production`, and use an SSH tunnel:
`ssh -N -L 58000:127.0.0.1:58000 root@SERVER_IP`. Then open localhost on
your Mac. Public HTTPS is enabled only after you apply the domain settings.

## Updating the deployed app

The current deployment is at `https://expressyourself.ai`, on
`2.29.23.252`, in `/opt/signalmap`.

That directory is a deployed copy, not a Git checkout: releases are shipped
there as an archive built from a commit. `docs/DEPLOYMENT.md` is the runbook
to deploy by, with the pre-flight checks, the expected numbers and the
rollback; this section is the shape of it.

### Ship a release

Decide the version bump (MAJOR/MINOR/PATCH) and tag it per
`docs/DEPLOYMENT.md` §0 first — move `CHANGELOG.md`'s `[Unreleased]`
block under the new version number before deploying.

```bash
# 1. Test locally first. Nothing reaches the server that did not pass here.
git checkout master && git pull --ff-only && docker compose up -d --build --wait

# 2. Back up the server database and pull it to this workstation.
ssh signalmap '/usr/local/bin/signalmap-backup.sh' && python tools/local/pull_backup.py

# 3. Build an archive of the commit, not of the working tree.
STAMP=$(date +%Y%m%d-%H%M%S) && SHA=$(git rev-parse HEAD) && ARCHIVE="/tmp/signalmap-$STAMP.tar.gz"
git archive --format=tar.gz -o "$ARCHIVE" HEAD
scp "$ARCHIVE" signalmap:/tmp/

# 4. Unpack it over the deployed copy, reinstall the host scripts, rebuild.
ssh signalmap "mkdir -p /tmp/signalmap-$STAMP && tar -xzf /tmp/signalmap-$STAMP.tar.gz -C /tmp/signalmap-$STAMP && rsync -a --delete --exclude='.env' --exclude='*.dump' --exclude='DEPLOYED_COMMIT' /tmp/signalmap-$STAMP/ /opt/signalmap/ && cd /opt/signalmap && ./tools/server/install.sh && docker compose up -d --build --wait"

# 5. Record what was deployed, clean up, verify.
ssh signalmap "echo $SHA > /opt/signalmap/DEPLOYED_COMMIT && echo \"\$(date -Is) $SHA\" >> /var/log/signalmap-deploys.log && rm -rf /tmp/signalmap-$STAMP /tmp/signalmap-$STAMP.tar.gz"
curl --fail --show-error https://expressyourself.ai/health
```

Why an archive rather than `rsync` straight from the working directory:
`git archive` exports from Git's object store, so uncommitted edits,
untracked files and CRLF line endings cannot travel with it. The `rsync`
in step 4 runs on the server, which is also why a Windows workstation does
not need `rsync` installed. The approach works unchanged if the repository
ever becomes private, and if the server has no access to GitHub.

Step 4's `./tools/server/install.sh` is not optional: it reinstalls the
host-side scripts (nightly backup, restricted SSH dispatcher) into
`/usr/local/bin`, and provisions `/var/lib/signalmap/maintenance` with the
Caddy-served maintenance page. Skip it and they silently drift from the
repository — on a fresh server, skipping it also leaves that directory
missing, so Caddy has nothing to serve or mount.

Step 5 is what makes the deployment traceable. An archive carries no
reference to the commit it came from, so without `DEPLOYED_COMMIT` there is
no way to answer what is running, and no target to roll back to.

Keep the server's `.env` and named volumes. Schema migrations run
automatically on app startup; no separate migration command is needed.
Rebuilding causes brief downtime, so avoid updates during provider runs.

SSH access must be handed over separately: install the maintainer's public
key and allow their IP in the Hetzner firewall's port-22 rule. The commands
above assume their SSH key is already configured. Do not put private keys
or tokens in this repository.

For an application rollback, ship the last known-good commit the same way,
building the archive from it instead of `HEAD`:

```bash
git archive --format=tar.gz -o /tmp/rollback.tar.gz <commit-from-DEPLOYED_COMMIT>
```

If an upgrade changed the schema, assess compatibility first; restoring the
pre-upgrade database may also be necessary. Restore into a fresh database as
described below, not over the running database.

The maintainer should also periodically install Ubuntu security updates,
reboot when required, and refresh the container images with
`docker compose build --pull app`, `docker compose pull postgres caddy`,
and `docker compose up -d --wait`, after a backup. Keep PostgreSQL on major
version 18 unless planning a separate database upgrade. Automatic releases
are not configured; scheduled backups are, see **Automated backups** below.

## Day-to-day commands

```bash
docker compose ps
docker compose logs --tail=100 -f app worker caddy
docker compose up -d --build --wait    # apply source or configuration changes
docker compose stop                  # stop; keep containers and data
docker compose up -d --wait           # start again
docker compose down                  # remove containers; keep data
```

Never use `docker compose down -v` unless you intend to delete the
entire database and Caddy certificate storage. Keep the Compose project
name `signalmap` to keep using the same named volumes. Changing
`POSTGRES_PASSWORD` alone does not rotate the password in an existing
Postgres volume; it is an initialization setting.

Take a database backup before applying upgrades: migrations run on every
app startup. This is a single-server deployment with brief downtime on
updates, not a rolling or high-availability setup. Avoid restarting while
provider runs are in progress.

### Scheduler worker

`worker` runs `app/worker.py` from the exact same image as `app` (no
separate build), never `alembic upgrade head` — only `app`'s own startup
command applies migrations, so the two containers never race for the
migration lock. It has no HTTP port: `docker compose ps` reports its
health from a liveness file it rewrites on every loop iteration, not a
web request. `docker compose stop worker` should return well under its
120s grace period — it finishes whatever queue item it's mid-processing
(a provider call can take up to ~30s) before exiting; a stop that hits the
full grace period and gets SIGKILLed means a stuck iteration, worth
checking `docker compose logs worker` for.

`SCHEDULER_DRY_RUN=true` (the default — see `.env.example`) plans and
queues runs for real but never calls a provider or spends money; stays on
until the scheduler UI and quota enforcement (docs/TASKS_SCHEDULER.md) are
both done. `SCHEDULER_ENABLED=false` stops the ticker from planning new
work while leaving the worker draining whatever is already queued — set
it on every instance but one if the app ever runs in more than one place
against the same database.

## Backup, restore, or transfer local data

Create a consistent database backup without stopping the app:

```bash
umask 077
docker compose exec -T postgres pg_dump -U signalmap_user -d signalmap -Fc > signalmap.dump
```

Copy the dump off the server and keep the private `.env` separately in a
safe place. A named volume survives container replacement, not loss of the
server.

### Automated backups

Scheduled backups are configured. The chain has three parts, all kept in
`tools/`:

| Where | What | When |
|---|---|---|
| server | `signalmap-backup.sh` under cron | 03:15 daily, keeps 14 days |
| server | `signalmap-batch.sh`, a forced SSH command | on request, read-only |
| workstation | `pull_backup.py` under Task Scheduler | 07:30 daily, keeps 90 days |

Install the host side with `./tools/server/install.sh` and add the cron line
from `tools/server/crontab.example`.

The workstation pulls over a dedicated passphrase-less SSH key pinned in
`authorized_keys` to `signalmap-batch.sh`, so it can list and read backups
and do nothing else. Retention on the server stays a root job under cron: a
key stored unattended on a workstation must never be able to delete backups.

`pull_backup.py` downloads every dump the workstation is missing, so a day
the PC was off is caught up rather than lost, and exits 2 when the newest
backup on the server is older than 48 hours, which is how a stopped cron
becomes visible instead of looking like "nothing new to download".

Verify that a backup is actually restorable with
`python tools/local/restore_local.py`. It restores into a separate database
and leaves the working one alone; an unverified backup is only a file you
believe is a backup.

For a new, empty deployment (including moving this local database to
Hetzner), copy `signalmap.dump` there, configure its `.env`, and restore
**before starting the app or creating an admin**:

```bash
docker compose up -d --wait postgres
docker compose exec -T postgres pg_restore --exit-on-error --no-owner --no-privileges -U signalmap_user -d signalmap < signalmap.dump
docker compose up -d --build --wait
```

The backup includes accounts and evidence; log in with the existing
account. Use new server secrets. An existing populated destination needs
a separate, deliberate replacement procedure; do not restore over it.

## Running tests

Tests use a separate real Postgres database, `signalmap_test`, and fake
provider adapters; they do not make billable AI calls. Create it once:

```bash
docker compose exec postgres psql -U signalmap_user -d signalmap -c "CREATE DATABASE signalmap_test;"
```

Run tests in a disposable container using the same app image. Stream the
test sources in so Docker Desktop does not need to share your source folder:

```bash
COPYFILE_DISABLE=1 tar -cf - tests requirements-dev.txt | docker compose run --rm --no-deps -T app sh -c 'tar -xf - && pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -p no:cacheprovider'
```

The serving container stays untouched. Tests create and drop their own
tables in `signalmap_test`; never use real evidence data in that database.

## Local deployment verification (2026-09-13)

- All three services healthy after a fresh build and after container recreation.
- All 19 migrations applied automatically; admin account persisted after `down`/`up`.
- HTTP health, login, anonymous-access protection, and forced first-password change verified through Caddy.
- Existing test suite: **124 passed** (two upstream deprecation warnings), using fake AI providers.
- Database dump restored into a separate empty database; migration version, account, and seed data verified.
- Deployed on Hetzner CPX22; production login, secure cookies, migrations, and service health verified. Public HTTPS `/health` returns `{"status":"ok"}` after DNS setup.
- AI provider calls require your API keys and were not made.

## Stack

FastAPI + SQLAlchemy + PostgreSQL, Jinja2 + HTMX, Tailwind via CDN,
Alembic migrations, a scheduler worker process, and Caddy, all managed by
one Docker Compose file.
