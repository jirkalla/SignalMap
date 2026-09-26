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
import subprocess
import sys

from _dbtools import COUNT_TABLES, DB_USER, DEST, compose, newest_dump, psql

CHECK_DB = "signalmap_restore_check"


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
