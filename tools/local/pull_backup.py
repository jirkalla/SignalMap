"""Pull SignalMap database backups from the server to this workstation.

Runs on the developer PC (Windows, Task Scheduler), not on the server. Talks to
the server only through the restricted `signalmap-batch` SSH key, which can read
backups and nothing else.

Downloads every dump the server has and this machine does not, so a day when the
PC was off or asleep is collected the next time it runs — not lost. It never
deletes anything on the server: retention there is the server's own job
(tools/server/signalmap-backup.sh under cron), and keeping this key read-only is
what makes it safe to store without a passphrase.

Exit codes matter — Task Scheduler shows them in "Last Run Result":
    0  everything fine
    1  failure (ssh failed, or a downloaded dump did not verify)
    2  finished, but the newest backup ON THE SERVER is stale, which means the
       server's own cron has stopped producing them. Without this check the
       symptom would look identical to "nothing new to download".

Configuration (environment variables, all optional):
    SIGNALMAP_BACKUP_DIR   where to store dumps   (default C:\\Backups\\SignalMap\\dumps)
    SIGNALMAP_SSH_HOST     ssh alias              (default signalmap-batch)
    SIGNALMAP_KEEP_DAYS    local retention        (default 90)
    SIGNALMAP_STALE_HOURS  staleness threshold    (default 48)

Standard library only — no venv, no requirements.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DEST = Path(os.environ.get("SIGNALMAP_BACKUP_DIR", r"C:\Backups\SignalMap\dumps"))
SSH_HOST = os.environ.get("SIGNALMAP_SSH_HOST", "signalmap-batch")
KEEP_DAYS = int(os.environ.get("SIGNALMAP_KEEP_DAYS", "90"))
STALE_HOURS = int(os.environ.get("SIGNALMAP_STALE_HOURS", "48"))

LOG_FILE = DEST.parent / "pull-backup.log"

# signalmap-20260917-133855.dump — the same shape tools/server/signalmap-batch.sh
# validates before serving a file, kept deliberately identical on both sides.
DUMP_RE = re.compile(r"^signalmap-(\d{8})-(\d{6})\.dump$")

# pg_dump custom-format archives start with this. Checking it needs no postgres
# client tools on Windows and catches the realistic failure — a truncated or
# empty transfer that would otherwise sit there looking like a backup.
PGDMP_MAGIC = b"PGDMP"
MIN_SIZE = 1024

SSH_TIMEOUT = 300


def log(line: str) -> None:
    """Write one timestamped line to stdout and to the log file."""
    stamped = f"{datetime.now().isoformat(timespec='seconds')} {line}"
    print(stamped)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(stamped + "\n")
    except OSError as exc:  # logging must never be the reason the backup fails
        print(f"(could not write {LOG_FILE}: {exc})")


def dump_taken_at(name: str) -> datetime | None:
    """Timestamp encoded in a dump's filename, or None if it isn't one of ours.

    Read from the name rather than from the file's mtime on purpose: mtime says
    when this machine downloaded it, the name says when the database was actually
    dumped — which is the only date that means anything for a backup.
    """
    match = DUMP_RE.match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S")
    except ValueError:
        return None


def ssh(operation: str, *, stdout=None) -> subprocess.CompletedProcess:
    """Run one whitelisted operation through the restricted key."""
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", SSH_HOST, operation],
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=SSH_TIMEOUT,
        check=True,
    )


def list_server_dumps() -> list[str]:
    result = ssh("backup-list")
    names = [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines()]
    return sorted(name for name in names if DUMP_RE.match(name))


def download(name: str) -> bool:
    """Fetch one dump. Returns True when it arrived complete and verified.

    Writes to a .tmp file and renames only after the archive verifies, so an
    interrupted transfer can never leave something behind that looks like a
    finished backup.
    """
    final = DEST / name
    tmp = DEST / (name + ".tmp")
    try:
        with tmp.open("wb") as fh:
            ssh(f"backup-get-{name}", stdout=fh)
        size = tmp.stat().st_size
        if size < MIN_SIZE:
            log(f"CHYBA {name}: soubor je podezrele maly ({size} B)")
            tmp.unlink(missing_ok=True)
            return False
        with tmp.open("rb") as fh:
            if fh.read(len(PGDMP_MAGIC)) != PGDMP_MAGIC:
                log(f"CHYBA {name}: chybi hlavicka PGDMP")
                tmp.unlink(missing_ok=True)
                return False
        tmp.replace(final)
        log(f"stazeno {name} ({size} B)")
        return True
    except subprocess.CalledProcessError as exc:
        log(f"CHYBA {name}: ssh selhalo: {exc.stderr.decode('utf-8', 'replace').strip()}")
    except subprocess.TimeoutExpired:
        log(f"CHYBA {name}: ssh vyprselo po {SSH_TIMEOUT}s")
    except OSError as exc:
        log(f"CHYBA {name}: {exc}")
    tmp.unlink(missing_ok=True)
    return False


def prune_local() -> int:
    """Delete local copies older than KEEP_DAYS. Never touches the server."""
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for path in DEST.glob("signalmap-*.dump"):
        taken = dump_taken_at(path.name)
        if taken is not None and taken < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                log(f"VAROVANI nepodarilo se smazat {path.name}: {exc}")
    return removed


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)

    try:
        server_dumps = list_server_dumps()
    except subprocess.CalledProcessError as exc:
        log(f"CHYBA ssh selhalo: {exc.stderr.decode('utf-8', 'replace').strip()}")
        return 1
    except subprocess.TimeoutExpired:
        log(f"CHYBA ssh vyprselo po {SSH_TIMEOUT}s")
        return 1
    except FileNotFoundError:
        log("CHYBA prikaz 'ssh' nenalezen v PATH")
        return 1

    if not server_dumps:
        log("VAROVANI server nema zadne zalohy")
        return 2

    local = {path.name for path in DEST.glob("signalmap-*.dump")}
    missing = [name for name in server_dumps if name not in local]

    failed = 0
    downloaded = 0
    for name in missing:
        if download(name):
            downloaded += 1
        else:
            failed += 1

    removed = prune_local()
    # Only files this script understands are counted (and, in prune_local, deleted).
    # Dumps with any other naming — an older manual backup dropped in the same
    # folder — are left strictly alone: not counted, not pruned, not touched.
    total_local = sum(1 for path in DEST.glob("signalmap-*.dump") if DUMP_RE.match(path.name))

    newest = max((dump_taken_at(n) for n in server_dumps if dump_taken_at(n)), default=None)
    age_hours = (datetime.now() - newest).total_seconds() / 3600 if newest else None

    summary = (
        f"stazeno={downloaded} preskoceno={len(server_dumps) - len(missing)} "
        f"chyb={failed} smazano={removed} lokalne={total_local}"
    )

    if failed:
        log(f"CHYBA {summary}")
        return 1
    if age_hours is not None and age_hours > STALE_HOURS:
        log(
            f"VAROVANI nejnovejsi zaloha na serveru je stara {age_hours:.0f} h "
            f"(limit {STALE_HOURS} h) — zkontroluj cron na serveru; {summary}"
        )
        return 2
    log(f"OK {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
