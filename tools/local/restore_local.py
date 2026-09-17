"""Verify a downloaded SignalMap backup by restoring it locally.

An unverified backup is not a backup — it is a file you believe is a backup.
This restores a dump into a SEPARATE database next to the local development one
(`signalmap_restore_check` by default), so proving that a backup works never
costs you your working data.

Run it occasionally, and always after changing anything in the backup chain:

    python tools/local/restore_local.py                    # newest dump
    python tools/local/restore_local.py --dump <filename>  # a specific one
    python tools/local/restore_local.py --keep             # leave the DB for inspection

Restoring over the real local database is possible but deliberate:

    python tools/local/restore_local.py --into signalmap --yes

Requires the local Compose stack to be running (`docker compose up -d`).
Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEST = Path(os.environ.get("SIGNALMAP_BACKUP_DIR", r"C:\Backups\SignalMap\dumps"))
PROJECT_DIR = Path(__file__).resolve().parents[2]
DB_USER = os.environ.get("SIGNALMAP_DB_USER", "signalmap_user")
CHECK_DB = "signalmap_restore_check"

# Same pattern as tools/local/pull_backup.py. Without it, an older manually named
# dump such as "signalmap-backup-20260916-062154.dump" sorts after the server's
# own "signalmap-20260917-141043.dump" and would silently be picked as "newest".
DUMP_RE = re.compile(r"^signalmap-\d{8}-\d{6}\.dump$")

# Tables every populated SignalMap database has — used to report that the restore
# produced actual content, not just an empty schema.
COUNT_TABLES = ("clients", "prompts", "runs", "raw_responses", "citations")


def compose(*args: str, stdin=None, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=PROJECT_DIR,
        stdin=stdin,
        stdout=subprocess.PIPE if capture else None,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", help="filename inside the backup directory (default: newest)")
    parser.add_argument("--into", default=CHECK_DB, help=f"target database (default: {CHECK_DB})")
    parser.add_argument("--keep", action="store_true", help="keep the check database afterwards")
    parser.add_argument("--yes", action="store_true", help="required when restoring into a database other than the check one")
    args = parser.parse_args()

    dump = DEST / args.dump if args.dump else newest_dump()
    if not dump.is_file():
        sys.exit(f"soubor neexistuje: {dump}")

    if args.into != CHECK_DB and not args.yes:
        sys.exit(
            f"obnova do '{args.into}' prepise existujici data - zopakuj s --yes, "
            f"nebo vynech --into a obnov do kontrolni databaze '{CHECK_DB}'"
        )

    print(f"obnovuji {dump.name} ({dump.stat().st_size} B) do databaze '{args.into}'")

    try:
        if args.into == CHECK_DB:
            # Always start from an empty database: a restore that silently merged
            # into leftovers from last time would prove nothing.
            psql(f'DROP DATABASE IF EXISTS "{CHECK_DB}"')
            psql(f'CREATE DATABASE "{CHECK_DB}"')

        with dump.open("rb") as fh:
            compose(
                "exec", "-T", "postgres",
                "pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
                "--clean", "--if-exists", "-U", DB_USER, "-d", args.into,
                stdin=fh,
            )

        print("\nobsah obnovene databaze:")
        for table in COUNT_TABLES:
            try:
                count = psql(f"SELECT count(*) FROM {table}", database=args.into)
                print(f"  {table:<16} {count}")
            except subprocess.CalledProcessError:
                print(f"  {table:<16} (tabulka chybi)")

    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        print(f"\nOBNOVA SELHALA: {stderr}", file=sys.stderr)
        print("Tahle zaloha NENI pouzitelna - zjisti proc, nez se na ni spolehnes.", file=sys.stderr)
        return 1
    except FileNotFoundError:
        sys.exit("prikaz 'docker' nenalezen - bezi Docker Desktop?")

    if args.into == CHECK_DB and not args.keep:
        psql(f'DROP DATABASE IF EXISTS "{CHECK_DB}"')
        print(f"\nkontrolni databaze '{CHECK_DB}' smazana (--keep ji zachova)")

    print("\nOK - zaloha je obnovitelna")
    return 0


if __name__ == "__main__":
    sys.exit(main())
