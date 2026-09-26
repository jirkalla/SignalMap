"""Load a production dump into the local development database, safely.

Runs on the developer PC (Windows), not on the server. The opposite job of
restore_local.py: that one proves a backup restores and never touches the
working database; this one deliberately replaces the working database — so it
backs it up first and makes sure the restored data cannot start anything
(docs/TASKS_DEV_DB_REFRESH.md, design decisions 1-16).

    python tools/local/refresh_dev_db.py                        # = refresh, newest dump
    python tools/local/refresh_dev_db.py refresh [DUMP] [--no-pull]
    python tools/local/refresh_dev_db.py restore-dev [SNAPSHOT]
    python tools/local/refresh_dev_db.py snapshot
    python tools/local/refresh_dev_db.py list

Common options: --plan (only print what would happen), --yes (skip the [y/N]
confirmation). DUMP / SNAPSHOT is empty (= newest), the start of a timestamp
("20260925", "20260925-1030") or a path to a dump file elsewhere on disk.

Refuses to run unless the value of SCHEDULER_DRY_RUN the worker would actually
get (read from `docker compose config`, so a shell variable and the Compose
default count too) is true. Never edits .env — only says what to change.

Configuration (environment variables, all optional):
    SIGNALMAP_BACKUP_DIR          prod dumps     (default C:\\Backups\\SignalMap\\dumps)
    SIGNALMAP_DEV_SNAPSHOT_DIR    dev snapshots  (default C:\\Backups\\SignalMap\\dev-snapshots)
    SIGNALMAP_DEV_SNAPSHOT_KEEP   snapshots kept (default 10)

Requires the local Compose stack to be running (`docker compose up -d`).
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from _dbtools import (
    COUNT_TABLES, DB_USER, DEST, DUMP_RE, MIN_SIZE, PGDMP_MAGIC, PROJECT_DIR, compose, psql,
)

SNAPSHOT_DIR = Path(
    os.environ.get("SIGNALMAP_DEV_SNAPSHOT_DIR", r"C:\Backups\SignalMap\dev-snapshots")
)
SNAPSHOT_KEEP = int(os.environ.get("SIGNALMAP_DEV_SNAPSHOT_KEEP", "10"))

# signalmap-local-20260926-081500-prod-20260925.dump — deliberately a shape DUMP_RE
# does not match, so a dev snapshot can never be mistaken for a prod dump (neither by
# pull_backup.py's retention nor by "newest dump" selection). Groups: date, time, origin.
SNAPSHOT_RE = re.compile(r"^signalmap-local-(\d{8})-(\d{6})-([a-z0-9]+(?:-[a-z0-9]+)*)\.dump$")

TARGET_DB = "signalmap"
# Restores land here first and replace TARGET_DB only once checked and sanitised
# (design decision 9), so a failed restore never touches the working database.
INCOMING_DB = "signalmap_incoming"

# Database comment recording where the data came from (design decision 8), e.g.
# "prod:signalmap-20260925-031500" after a refresh, or "prod:signalmap-20260925" after
# restore-dev of a snapshot that held prod data. Anything else, or none, means dev data.
PROD_COMMENT_RE = re.compile(r"^prod:signalmap-(\d{8})(?:-\d{6})?$")

PULL_SCRIPT = Path(__file__).resolve().with_name("pull_backup.py")
HEARTBEAT_TIMEOUT_SECONDS = 60

# Design decisions 10b and 12, applied in INCOMING_DB before the swap, so TARGET_DB
# never holds unsanitised prod data. One statement: data-modifying CTEs run in a single
# transaction, all or nothing. Values come from alembic/versions/0030_scheduler.py
# (run_queue CHECK) and app/models/notification.py (outbox, no CHECK); 'suppressed' is
# the documented status for a backlog a channel must not deliver. runs are evidence and
# are only counted, never changed (AI_INSTRUCTIONS section 3).
SANITIZE_SQL = """
WITH schedules AS (
    UPDATE run_schedules
       SET is_active = false,
           inactive_reason = 'user',
           next_run_at = NULL,
           updated_at = now()
     WHERE is_active
    RETURNING 1
), queue AS (
    UPDATE run_queue
       SET status = 'cancelled',
           finished_at = now(),
           leased_by = NULL,
           leased_until = NULL
     WHERE status IN ('queued', 'leased', 'deferred')
    RETURNING 1
), heartbeats AS (
    DELETE FROM worker_heartbeats
    RETURNING 1
), outbox AS (
    UPDATE notification_outbox
       SET status = 'suppressed'
     WHERE status = 'pending'
    RETURNING 1
)
SELECT (SELECT count(*) FROM schedules),
       (SELECT count(*) FROM queue),
       (SELECT count(*) FROM heartbeats),
       (SELECT count(*) FROM outbox),
       (SELECT count(*) FROM runs WHERE status = 'pending')
"""

# pydantic v2's own bool parsing (case-insensitive), which is how app/config.py reads
# SCHEDULER_DRY_RUN. Anything outside both sets makes the worker fail to start.
PYDANTIC_TRUE = frozenset({"1", "on", "t", "true", "y", "yes"})
PYDANTIC_FALSE = frozenset({"0", "off", "f", "false", "n", "no"})

LOCAL_HOST_IPS = frozenset({"127.0.0.1", "::1"})

ALEMBIC_REVISION_RE = re.compile(r"""^revision\s*(?::\s*str\s*)?=\s*["']([^"']+)["']""", re.M)

COMMANDS = ("refresh", "restore-dev", "snapshot", "list")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def stamp_of(path: Path, pattern: re.Pattern[str]) -> str:
    """'YYYYMMDD-HHMMSS' from a dump or snapshot filename (caller ensures it matches)."""
    match = pattern.match(path.name)
    return f"{match.group(1)}-{match.group(2)}"


def taken_at(path: Path, pattern: re.Pattern[str]) -> datetime | None:
    try:
        return datetime.strptime(stamp_of(path, pattern), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def list_files(folder: Path, pattern: re.Pattern[str]) -> list[Path]:
    """Files in `folder` matching `pattern`, oldest first. Anything else is ignored."""
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and pattern.match(p.name)]
    return sorted(files, key=lambda p: stamp_of(p, pattern))


def has_pgdmp_header(path: Path) -> bool:
    try:
        if path.stat().st_size < MIN_SIZE:
            return False
        with path.open("rb") as fh:
            return fh.read(len(PGDMP_MAGIC)) == PGDMP_MAGIC
    except OSError:
        return False


def select_file(arg: str | None, folder: Path, pattern: re.Pattern[str], kind: str) -> Path:
    """Resolve a DUMP / SNAPSHOT argument to a file, or exit explaining why not.

    Empty = newest. An existing path is used as-is if it is a pg_dump archive.
    Otherwise the argument must be the start of exactly one timestamp — several
    matches are listed and the script stops rather than guessing.
    """
    files = list_files(folder, pattern)
    if not arg:
        if not files:
            sys.exit(f"zadne {kind} v {folder}")
        return files[-1]

    for candidate in (Path(arg), folder / arg):
        if candidate.is_file():
            if not has_pgdmp_header(candidate):
                sys.exit(f"{candidate} neni pg_dump archiv (chybi hlavicka PGDMP nebo je prilis maly)")
            return candidate.resolve()

    matches = [p for p in files if stamp_of(p, pattern).startswith(arg)]
    if not matches:
        sys.exit(f"'{arg}' neodpovida zadnemu souboru ani zacatku casoveho razitka v {folder}")
    if len(matches) > 1:
        print(f"'{arg}' odpovida vic souborum ({len(matches)}), upresni razitko:", file=sys.stderr)
        for path in matches:
            print(f"  {path.name}", file=sys.stderr)
        sys.exit(1)
    return matches[0]


def size_mb(path: Path) -> str:
    return f"{path.stat().st_size / 1024 / 1024:.1f} MB"


# ---------------------------------------------------------------------------
# Database state
# ---------------------------------------------------------------------------


def db_comment() -> str:
    return psql(
        "SELECT coalesce(shobj_description(oid, 'pg_database'), '') "
        f"FROM pg_database WHERE datname = '{TARGET_DB}'"
    )


def origin_of(comment: str) -> str:
    """Origin token used in a snapshot's filename: 'prod-YYYYMMDD', 'prod' or 'dev'."""
    match = PROD_COMMENT_RE.match(comment)
    if match:
        return f"prod-{match.group(1)}"
    return "prod" if comment.startswith("prod:") else "dev"


def comment_for(source: Path, is_refresh: bool) -> str:
    """Database comment to write after restoring `source` (design decision 8)."""
    if is_refresh:
        return f"prod:{source.stem}"
    match = SNAPSHOT_RE.match(source.name)
    origin = match.group(3) if match else "dev"
    if origin.startswith("prod-"):
        return f"prod:signalmap-{origin[len('prod-'):]}"
    return "prod:unknown" if origin == "prod" else "dev"


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def open_connections() -> list[str]:
    """Sessions connected to the target database, one printable line each."""
    out = psql(
        "SELECT pid, usename, coalesce(nullif(application_name, ''), '-'), "
        "coalesce(host(client_addr), 'local'), coalesce(state, '-') "
        f"FROM pg_stat_activity WHERE datname = '{TARGET_DB}' AND pid <> pg_backend_pid() "
        "ORDER BY pid"
    )
    return [" ".join(line.split("|")) for line in out.splitlines() if line]


def snapshot_path(origin: str, now: datetime) -> Path:
    return SNAPSHOT_DIR / f"signalmap-local-{now:%Y%m%d-%H%M%S}-{origin}.dump"


def take_snapshot() -> Path:
    """pg_dump the target database into the snapshot folder and apply retention.

    Written to a .tmp file and renamed only after the archive header verifies, so
    an interrupted dump never leaves something that looks like a finished snapshot.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    final = snapshot_path(origin_of(db_comment()), datetime.now())
    tmp = final.with_name(final.name + ".tmp")
    try:
        with tmp.open("wb") as fh:
            compose(
                "exec", "-T", "postgres", "pg_dump", "-Fc", "-U", DB_USER, "-d", TARGET_DB,
                stdout=fh,
            )
        if not has_pgdmp_header(tmp):
            raise RuntimeError(f"{tmp.name}: chybi hlavicka PGDMP nebo je prilis maly")
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)
    return final


def prune_snapshots(protect: Path | None = None) -> list[Path]:
    """Keep the newest SNAPSHOT_KEEP snapshots; only files matching SNAPSHOT_RE are touched.

    `protect` is never deleted even if it falls outside the window — the snapshot
    a restore-dev is about to read from must survive the backup taken right before it.
    """
    files = list_files(SNAPSHOT_DIR, SNAPSHOT_RE)
    doomed = files[:-SNAPSHOT_KEEP] if SNAPSHOT_KEEP > 0 else []
    removed = []
    for path in doomed:
        if protect is not None and path.resolve() == protect.resolve():
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError as exc:
            print(f"VAROVANI nepodarilo se smazat {path.name}: {exc}", file=sys.stderr)
    return removed


# ---------------------------------------------------------------------------
# Checks — each returns a list of problems, so --plan can show all of them at once
# ---------------------------------------------------------------------------


def compose_config(env: dict[str, str] | None = None) -> dict:
    """The fully resolved Compose config. Contains filled-in secrets: never print it."""
    result = compose("config", "--format", "json", env=env)
    return json.loads(result.stdout.decode("utf-8", "replace"))


def parse_bool(value: object) -> bool | None:
    """Mirror pydantic's bool parsing; None = the worker would reject the value."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if lowered in PYDANTIC_TRUE:
        return True
    if lowered in PYDANTIC_FALSE:
        return False
    return None


def worker_dry_run(config: dict) -> object:
    return (config.get("services", {}).get("worker", {}).get("environment") or {}).get(
        "SCHEDULER_DRY_RUN"
    )


def check_compose(config: dict) -> list[str]:
    """Design decisions 10a and 13: dry-run worker, development environment, local ports."""
    problems = []
    services = config.get("services", {})

    if "worker" not in services:
        problems.append("Compose nema sluzbu 'worker' - nelze overit SCHEDULER_DRY_RUN")
    else:
        raw = worker_dry_run(config)
        if parse_bool(raw) is not True:
            shown = "(nenastaveno)" if raw is None else repr(raw)
            problems.append(
                f"worker by dostal SCHEDULER_DRY_RUN={shown} - nastav v .env "
                "SCHEDULER_DRY_RUN=true (skript .env nemeni)"
            )

    for name in ("app", "worker"):
        env = (services.get(name, {}).get("environment") or {})
        value = env.get("ENVIRONMENT")
        if value != "development":
            problems.append(f"sluzba '{name}' ma ENVIRONMENT={value!r}, ocekavano 'development'")

    for name, service in services.items():
        for port in service.get("ports") or []:
            host_ip = port.get("host_ip")
            if host_ip not in LOCAL_HOST_IPS:
                problems.append(
                    f"sluzba '{name}' publikuje port {port.get('published')} na "
                    f"{host_ip or 'vsech rozhranich'}, ocekavano 127.0.0.1"
                )
    return problems


def check_dry_run_source(config: dict) -> list[str]:
    """Warn (not fail) when dry-run is true only because of this shell's variable.

    The check above accepts it, but a plain `docker compose up` from another
    terminal would then start the worker with .env's value over the restored data.
    """
    if "SCHEDULER_DRY_RUN" not in os.environ or parse_bool(worker_dry_run(config)) is not True:
        return []
    env = {k: v for k, v in os.environ.items() if k != "SCHEDULER_DRY_RUN"}
    try:
        without_shell = parse_bool(worker_dry_run(compose_config(env)))
    except (subprocess.CalledProcessError, ValueError):
        return []
    if without_shell is True:
        return []
    return [
        "SCHEDULER_DRY_RUN=true plati jen diky promenne v tomhle shellu - obycejne "
        "`docker compose up` z jineho terminalu spusti workera bez dry-run; "
        "zvaz SCHEDULER_DRY_RUN=true primo v .env"
    ]


def local_revisions() -> set[str]:
    revisions = set()
    for path in (PROJECT_DIR / "alembic" / "versions").glob("*.py"):
        match = ALEMBIC_REVISION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            revisions.add(match.group(1))
    return revisions


def dump_revision(dump: Path) -> str | None:
    """alembic_version of a dump, read without restoring anything.

    `pg_restore -f -` only prints the table's COPY block as SQL to stdout — no
    database is created or written.
    """
    with dump.open("rb") as fh:
        result = compose(
            "exec", "-T", "postgres",
            "pg_restore", "--data-only", "-t", "alembic_version", "-f", "-",
            stdin=fh,
        )
    in_copy = False
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("COPY ") and "alembic_version" in line:
            in_copy = True
        elif in_copy:
            return None if line == "\\." else line.strip()
    return None


def check_alembic(dump: Path) -> tuple[str | None, list[str]]:
    """Design decision 15: refuse a dump from a schema newer than this checkout."""
    try:
        revision = dump_revision(dump)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        return None, [f"nepodarilo se precist alembic_version z {dump.name}: {stderr}"]
    if revision is None:
        return None, [f"{dump.name} neobsahuje zadnou revizi v alembic_version"]
    if revision not in local_revisions():
        return revision, [
            f"dump je na revizi {revision}, kterou tahle vetev nezna (alembic/versions/) - "
            "produkce je novejsi nez lokalni kod, prejdi na aktualni master"
        ]
    return revision, []


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def print_table(title: str, folder: Path, files: list[Path], pattern: re.Pattern[str], *, origin: bool) -> None:
    print(f"{title} ({folder})")
    if not folder.is_dir():
        print("  (slozka neexistuje)")
        return
    if not files:
        print("  (zadne soubory)")
        return
    for path in files:
        when = taken_at(path, pattern)
        when_s = when.strftime("%Y-%m-%d %H:%M:%S") if when else "?"
        extra = f"  {pattern.match(path.name).group(3):<14}" if origin else ""
        newest = "  <- nejnovejsi" if path == files[-1] else ""
        print(f"  {when_s}  {size_mb(path):>9}{extra}  {path.name}{newest}")


def cmd_list(_args: argparse.Namespace) -> int:
    print_table("prod dumpy", DEST, list_files(DEST, DUMP_RE), DUMP_RE, origin=False)
    print()
    print_table("dev zalohy", SNAPSHOT_DIR, list_files(SNAPSHOT_DIR, SNAPSHOT_RE), SNAPSHOT_RE, origin=True)
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    if args.plan:
        print(f"zazalohoval bych databazi '{TARGET_DB}' do {SNAPSHOT_DIR}")
        print(f"(retence: poslednich {SNAPSHOT_KEEP} zaloh)")
        return 0
    path = take_snapshot()
    print(f"zaloha vytvorena: {path} ({size_mb(path)})")
    for removed in prune_snapshots():
        print(f"  retence smazala {removed.name}")
    return 0


def confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def describe(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        return f"{' '.join(map(str, exc.cmd[2:7]))} ... selhal (kod {exc.returncode}): {stderr}"
    return str(exc)


def preflight() -> tuple[list[str], list[str], str, list[str]]:
    """Checks that need no dump (decisions 10a, 13) plus the target's current state.

    Returns (problems, warnings, current db comment, open connections).
    """
    problems: list[str] = []
    warnings: list[str] = []
    try:
        config = compose_config()
        problems += check_compose(config)
        warnings += check_dry_run_source(config)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        problems.append(f"`docker compose config` selhal: {stderr}")

    try:
        comment = db_comment()
        connections = open_connections()
    except subprocess.CalledProcessError:
        problems.append("nelze se pripojit k postgres - bezi `docker compose up -d`?")
        comment, connections = "", []
    return problems, warnings, comment, connections


def print_checks(problems: list[str], warnings: list[str]) -> None:
    print("\nkontroly:")
    for warning in warnings:
        print(f"  VAROVANI  {warning}")
    for problem in problems:
        print(f"  CHYBA     {problem}")
    if not problems:
        print("  OK        dry-run workera, lokalni prostredi, revize alembic")


def run_pull() -> None:
    """Fetch new prod dumps first (T3 step 2). Exit 2 (stale server backup) only warns."""
    print("stahuji nove zalohy ze serveru (pull_backup.py) ...")
    code = subprocess.run([sys.executable, str(PULL_SCRIPT)]).returncode
    if code == 2:
        print("VAROVANI pull_backup.py skoncil kodem 2 (zastarala zaloha na serveru) - pokracuji")
    elif code != 0:
        sys.exit(
            f"pull_backup.py selhal (kod {code}) - nic se nezmenilo. "
            "Zopakuj s --no-pull pro posledni uz stazeny dump."
        )
    print()


def restore_into_incoming(source: Path) -> None:
    """Fresh INCOMING_DB with `source` restored into it. TARGET_DB is not touched."""
    psql(f'DROP DATABASE IF EXISTS "{INCOMING_DB}" WITH (FORCE)')
    psql(f'CREATE DATABASE "{INCOMING_DB}"')
    with source.open("rb") as fh:
        compose(
            "exec", "-T", "postgres",
            "pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
            "-U", DB_USER, "-d", INCOMING_DB,
            stdin=fh,
        )


def content_counts(database: str) -> dict[str, int]:
    """Row counts proving the restore produced real content, not just a schema."""
    counts = {}
    for table in COUNT_TABLES:
        try:
            counts[table] = int(psql(f"SELECT count(*) FROM {table}", database=database))
        except subprocess.CalledProcessError:
            raise RuntimeError(f"obnovena databaze nema tabulku {table}") from None
    if sum(counts.values()) == 0:
        raise RuntimeError(f"obnovena databaze je prazdna ({', '.join(COUNT_TABLES)} = 0)")
    if not psql("SELECT version_num FROM alembic_version", database=database):
        raise RuntimeError("obnovena databaze ma prazdnou tabulku alembic_version")
    return counts


def sanitize(database: str) -> dict[str, int]:
    out = psql(SANITIZE_SQL, database=database)
    schedules, queue, heartbeats, outbox, pending = (int(x) for x in out.split("|"))
    return {
        "schedules": schedules, "queue": queue, "heartbeats": heartbeats,
        "outbox": outbox, "pending_runs": pending,
    }


def wait_for_heartbeat(since: str) -> list[str]:
    """Design decision 10c: the running worker itself reports dry_run = true.

    Only rows written after `since` (the database's own clock just before the start)
    count — never a row that merely exists. Returns the worker names seen; raises
    RuntimeError on a non-dry-run worker or on timeout.
    """
    deadline = time.monotonic() + HEARTBEAT_TIMEOUT_SECONDS
    while True:
        out = psql(
            "SELECT worker_name, dry_run FROM worker_heartbeats "
            f"WHERE last_seen_at > {sql_literal(since)}::timestamptz",
            database=TARGET_DB,
        )
        rows = [line.split("|") for line in out.splitlines() if line]
        if rows:
            wrong = [name for name, dry_run in rows if dry_run != "t"]
            if wrong:
                raise RuntimeError(f"worker {', '.join(wrong)} bezi BEZ dry-run")
            return [name for name, _ in rows]
        if time.monotonic() > deadline:
            raise RuntimeError(f"zadny heartbeat workera do {HEARTBEAT_TIMEOUT_SECONDS} s")
        time.sleep(2)


def cmd_overwrite(args: argparse.Namespace) -> int:
    """Shared flow of `refresh` and `restore-dev` (docs/TASKS_DEV_DB_REFRESH.md T3).

    The order is the safety argument: checks before anything is downloaded or
    stopped, a backup before anything is overwritten, sanitising before the swap,
    and the worker's own heartbeat checked after the start.
    """
    is_refresh = args.command == "refresh"

    # Decision 5: never wait for an answer nobody can give.
    if not args.plan and not args.yes and not sys.stdin.isatty():
        sys.exit("stdin neni terminal a chybi --yes - potvrzeni nelze ziskat, koncim")

    # 1. Checks that need no dump — before pull_backup.py downloads anything.
    problems, warnings, comment, connections = preflight()

    # 2. Newest prod dumps first, unless told otherwise. Never in --plan.
    will_pull = is_refresh and not args.no_pull and not args.dump
    if will_pull and not args.plan:
        if problems:
            print_checks(problems, warnings)
            print(f"\n{len(problems)} problem(u) - nic se nestahlo ani nezmenilo.")
            return 1
        run_pull()

    # Selected before the backup below, so restore-dev's "newest" is never the backup itself.
    if is_refresh:
        source = select_file(args.dump, DEST, DUMP_RE, "prod dumpy")
    else:
        source = select_file(args.snapshot, SNAPSHOT_DIR, SNAPSHOT_RE, "dev zalohy")
    revision, alembic_problems = check_alembic(source)
    problems += alembic_problems

    # 3. Plan + confirmation.
    backup_preview = snapshot_path(origin_of(comment), datetime.now())
    # A file given by path may be named anything; only a recognised name carries a date.
    pattern = DUMP_RE if is_refresh else SNAPSHOT_RE
    when = taken_at(source, pattern) if pattern.match(source.name) else None

    print(f"PLAN: {args.command}")
    print(f"  zdroj     {source}")
    print(f"            {size_mb(source)}"
          + (f", porizeno {when:%Y-%m-%d %H:%M:%S}" if when else "")
          + f", alembic {revision or '?'}")
    if will_pull:
        if args.plan:
            print("  stazeni   pred obnovou se spusti pull_backup.py; kdyz stahne novejsi dump,")
            print("            pouzije se ten (--no-pull nebo zadany DUMP to vypne)")
        else:
            print("  stazeni   pull_backup.py probehl, zdroj je nejnovejsi dump po stazeni")
    print(f"  cil       databaze '{TARGET_DB}' (puvod: {origin_of(comment)}"
          + (f", komentar '{comment}'" if comment else "") + ")")
    print(f"            po obnove: komentar '{comment_for(source, is_refresh)}'")
    print(f"  zaloha    {backup_preview}")
    print(f"            (pred prepisem, vzdy; retence poslednich {SNAPSHOT_KEEP})")
    if connections:
        print(f"  spojeni   {len(connections)} k '{TARGET_DB}' - app/worker se zastavi, zbytek se ukonci:")
        for line in connections:
            print(f"              {line}")
    else:
        print(f"  spojeni   zadna k '{TARGET_DB}'")
    print("  uprava    rozvrhy vypnout, frontu zrusit, heartbeaty smazat, pending upozorneni")
    print(f"            potlacit - v '{INCOMING_DB}', pred vymenou")

    print_checks(problems, warnings)

    if problems:
        print(f"\n{len(problems)} problem(u) - nic se nezmenilo.")
        return 1
    if args.plan:
        print("\n--plan: nic se nezmenilo.")
        return 0

    if not args.yes and not confirm(f"\nPrepsat databazi '{TARGET_DB}'?"):
        print("zruseno - nic se nezmenilo.")
        return 1

    # 4. Backup of the current database — no backup, no overwrite (decision 6).
    print(f"\nzalohuji databazi '{TARGET_DB}' ...")
    try:
        backup = take_snapshot()
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        print(f"ZALOHA SELHALA - nic se nezmenilo: {describe(exc)}", file=sys.stderr)
        return 1
    print(f"  {backup} ({size_mb(backup)})")
    for removed in prune_snapshots(protect=source):
        print(f"  retence smazala {removed.name}")

    # 5-8. Stop, restore into INCOMING_DB, check + sanitise there, then swap.
    dropped = False
    try:
        print("zastavuji app a worker ...")
        compose("stop", "app", "worker")
        print(f"obnovuji {source.name} do '{INCOMING_DB}' ...")
        restore_into_incoming(source)
        counts = content_counts(INCOMING_DB)
        print(f"upravuji data v '{INCOMING_DB}' ...")
        sanitized = sanitize(INCOMING_DB)
        print(f"vymenuji '{INCOMING_DB}' -> '{TARGET_DB}' ...")
        # FORCE ends leftover sessions (e.g. DBeaver) atomically with the drop (decision 14).
        psql(f'DROP DATABASE "{TARGET_DB}" WITH (FORCE)')
        dropped = True
        psql(f'ALTER DATABASE "{INCOMING_DB}" RENAME TO "{TARGET_DB}"')
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        print(f"\nOBNOVA SELHALA: {describe(exc)}", file=sys.stderr)
        try:
            psql(f'DROP DATABASE IF EXISTS "{INCOMING_DB}" WITH (FORCE)')
            print(f"'{INCOMING_DB}' uklizena.", file=sys.stderr)
        except subprocess.CalledProcessError as cleanup_exc:
            print(f"'{INCOMING_DB}' se nepodarilo smazat: {describe(cleanup_exc)}", file=sys.stderr)
        if dropped:
            print(
                f"POZOR: '{TARGET_DB}' uz byla smazana, ale prejmenovani selhalo. "
                f"Obnov ji: python tools/local/refresh_dev_db.py restore-dev {backup}",
                file=sys.stderr,
            )
        else:
            print(f"'{TARGET_DB}' zustala beze zmeny.", file=sys.stderr)
        print(f"zaloha: {backup}", file=sys.stderr)
        print(
            "app a worker zustavaji zastavene - az zjistis pricinu: docker compose up -d",
            file=sys.stderr,
        )
        return 1

    try:
        psql(f'COMMENT ON DATABASE "{TARGET_DB}" IS {sql_literal(comment_for(source, is_refresh))}')
    except subprocess.CalledProcessError as exc:
        print(f"VAROVANI komentar databaze se nezapsal: {describe(exc)}", file=sys.stderr)

    # 10. Start with dry-run forced in this process's environment (second safeguard).
    since = psql("SELECT now()")
    print("spoustim app a worker (SCHEDULER_DRY_RUN=true) ...")
    env = dict(os.environ, SCHEDULER_DRY_RUN="true")
    try:
        compose("up", "-d", "--wait", "app", "worker", env=env)
    except subprocess.CalledProcessError as exc:
        print(f"START SELHAL: {describe(exc)}", file=sys.stderr)
        compose("stop", "worker")
        print(f"worker zastaven. Data jsou obnovena, zaloha: {backup}", file=sys.stderr)
        return 1

    # 11. Dev admin, since the prod dump carries only prod users (decision 16).
    try:
        result = compose("exec", "-T", "app", "python", "-m", "scripts.create_admin", "--from-env")
        # ASCII only: the script's output goes to a Windows console of unknown code page.
        message = result.stdout.decode("utf-8", "replace").replace("—", "-").strip()
        print(f"  {message.encode('ascii', 'replace').decode('ascii')}")
    except subprocess.CalledProcessError as exc:
        print(f"VAROVANI create_admin --from-env selhal: {describe(exc)}", file=sys.stderr)

    # 12. The worker itself must report dry-run (third safeguard).
    print("cekam na heartbeat workera ...")
    try:
        workers = wait_for_heartbeat(since)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        compose("stop", "worker")
        print(f"\nKONTROLA DRY-RUN SELHALA: {describe(exc)}", file=sys.stderr)
        print(f"worker zastaven. Zaloha: {backup}", file=sys.stderr)
        return 1

    # 13. Summary.
    print(f"\nOK - '{TARGET_DB}' obsahuje {source.name}")
    print(f"  puvod       {comment_for(source, is_refresh)}")
    for table, count in counts.items():
        print(f"  {table:<16} {count}")
    print(f"  zaloha      {backup}")
    print(f"  rozvrhy     vypnuto {sanitized['schedules']}")
    print(f"  fronta      zruseno {sanitized['queue']}")
    print(f"  upozorneni  potlaceno {sanitized['outbox']}")
    print(f"  heartbeaty  smazano {sanitized['heartbeats']}; dry-run potvrdil: {', '.join(workers)}")
    if sanitized["pending_runs"]:
        print(
            f"  pending     {sanitized['pending_runs']} behu - worker ty starsi nez 30 min oznaci jako "
            "error (reconcile_interrupted_runs), stejne jako by to udelala produkce"
        )
    else:
        print("  pending     0 behu")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--plan", action="store_true", help="only print what would happen, change nothing")
    common.add_argument("--yes", action="store_true", help="skip the [y/N] confirmation")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    refresh = sub.add_parser("refresh", parents=[common], help="load a prod dump into the dev database (default)")
    refresh.add_argument("dump", nargs="?", help="timestamp prefix or path (default: newest)")
    refresh.add_argument("--no-pull", action="store_true", help="do not run pull_backup.py first")
    refresh.set_defaults(func=cmd_overwrite)

    restore = sub.add_parser("restore-dev", parents=[common], help="put a dev snapshot back")
    restore.add_argument("snapshot", nargs="?", help="timestamp prefix or path (default: newest)")
    restore.set_defaults(func=cmd_overwrite)

    snapshot = sub.add_parser("snapshot", parents=[common], help="back up the dev database now")
    snapshot.set_defaults(func=cmd_snapshot)

    listing = sub.add_parser("list", parents=[common], help="show prod dumps and dev snapshots")
    listing.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Progress (stdout) and errors (stderr) must interleave in order even when redirected.
    sys.stdout.reconfigure(line_buffering=True)
    argv = list(sys.argv[1:] if argv is None else argv)
    # No subcommand (or just options / a DUMP) means `refresh`.
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
        argv.insert(0, "refresh")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        if getattr(exc, "filename", None) in ("docker", None):
            sys.exit("prikaz 'docker' nenalezen - bezi Docker Desktop?")
        raise
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        sys.exit(f"CHYBA: {' '.join(map(str, exc.cmd[:4]))} selhal: {stderr}")
    except RuntimeError as exc:
        sys.exit(f"CHYBA: {exc}")


if __name__ == "__main__":
    sys.exit(main())
