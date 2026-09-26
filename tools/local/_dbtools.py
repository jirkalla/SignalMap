"""Shared helpers for the local database scripts in tools/local/.

Imported as a plain `import _dbtools` — the scripts are run as
`python tools/local/<script>.py`, which puts this folder on sys.path.

pull_backup.py deliberately does NOT import from here, even though it defines
the same DEST, DUMP_RE and PGDMP_MAGIC/MIN_SIZE. It runs unattended every
morning under Task Scheduler, and a new import is a new way for it to fail that
would only show up as backups quietly missing. Keep the definitions identical
by hand.

Standard library only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DEST = Path(os.environ.get("SIGNALMAP_BACKUP_DIR", r"C:\Backups\SignalMap\dumps"))
PROJECT_DIR = Path(__file__).resolve().parents[2]
DB_USER = os.environ.get("SIGNALMAP_DB_USER", "signalmap_user")

# Same pattern as tools/local/pull_backup.py (and tools/server/signalmap-batch.sh).
# Without it, an older manually named dump such as
# "signalmap-backup-20260916-062154.dump" sorts after the server's own
# "signalmap-20260917-141043.dump" and would silently be picked as "newest".
# Groups: date (YYYYMMDD), time (HHMMSS).
DUMP_RE = re.compile(r"^signalmap-(\d{8})-(\d{6})\.dump$")

# Tables every populated SignalMap database has — used to report that a restore
# produced actual content, not just an empty schema.
COUNT_TABLES = ("clients", "prompts", "runs", "raw_responses", "citations")

# pg_dump custom-format archives start with this; same check as pull_backup.py.
# Catches a truncated or empty dump without needing postgres client tools on Windows.
PGDMP_MAGIC = b"PGDMP"
MIN_SIZE = 1024


def compose(
    *args: str, stdin=None, stdout=None, capture: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run `docker compose <args>` in the project directory, raising on failure.

    `stdout` (an open file) overrides capturing, for streaming a dump to disk.
    `env` replaces the process environment, which Compose gives precedence over .env.
    """
    if stdout is None:
        stdout = subprocess.PIPE if capture else None
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=PROJECT_DIR,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE if capture else None,
        check=True,
    )


def psql(sql: str, *, database: str = "postgres") -> str:
    result = compose("exec", "-T", "postgres", "psql", "-U", DB_USER, "-d", database, "-tAc", sql)
    return result.stdout.decode("utf-8", "replace").strip()


def newest_dump() -> Path:
    dumps = sorted(p for p in DEST.glob("signalmap-*.dump") if DUMP_RE.match(p.name))
    if not dumps:
        sys.exit(f"zadne zalohy v {DEST} - spust nejdriv tools/local/pull_backup.py")
    return dumps[-1]
