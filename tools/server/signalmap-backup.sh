#!/bin/sh
# Nightly SignalMap database backup.
#
# Installed to /usr/local/bin/signalmap-backup.sh by tools/server/install.sh and
# run from cron (see crontab.example). The canonical copy lives in the repository
# — edit it there, never directly on the server, or the next deployment reverts it.
#
# Writes a compressed pg_dump custom-format archive to /var/backups/signalmap/,
# keeps RETENTION_DAYS of history and deletes older files. This local copy only
# protects against mistakes (a deleted client, a bad migration), never against
# losing the server itself — the off-server copy pulled by tools/local does that.
set -eu

PROJECT_DIR=${PROJECT_DIR:-/opt/signalmap}
OUT=${OUT:-/var/backups/signalmap}
RETENTION_DAYS=${RETENTION_DAYS:-14}
DB_NAME=${DB_NAME:-signalmap}
DB_USER=${DB_USER:-signalmap_user}
DOCKER=${DOCKER:-/usr/bin/docker}

# cron gives the script a near-empty PATH, so every binary is called by full path.
umask 077

STAMP=$(date +%Y%m%d-%H%M%S)
TMP="$OUT/signalmap-$STAMP.dump.tmp"
FINAL="$OUT/signalmap-$STAMP.dump"

log() { echo "$(date +%FT%T) $*"; logger -t signalmap-backup "$*"; }
fail() { log "FAILED: $*"; rm -f "$TMP"; exit 1; }

mkdir -p "$OUT"
chmod 700 "$OUT"
cd "$PROJECT_DIR" || fail "project directory $PROJECT_DIR not found"

log "starting dump of $DB_NAME"

# -T: no TTY, required under cron. -Fc: compressed custom format, restorable
# table by table with pg_restore.
"$DOCKER" compose exec -T postgres \
    pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$TMP" || fail "pg_dump returned an error"

# Verify before publishing the file: a truncated dump that merely exists is worse
# than no dump, because it looks like a backup. PGDMP is the custom-format magic
# header — checking it needs no postgres client tools on the host.
[ -s "$TMP" ] || fail "dump is empty"
[ "$(head -c 5 "$TMP")" = "PGDMP" ] || fail "dump does not start with the PGDMP header"

SIZE=$(wc -c < "$TMP")
[ "$SIZE" -gt 1024 ] || fail "dump is suspiciously small ($SIZE bytes)"

# Only now does the file get its real name — a half-written dump is never
# mistaken for a finished one, not even if the server dies mid-write.
mv "$TMP" "$FINAL"
log "wrote $FINAL ($SIZE bytes)"

DELETED=$(find "$OUT" -name 'signalmap-*.dump' -mtime "+$RETENTION_DAYS" -print -delete | wc -l)
[ "$DELETED" -eq 0 ] || log "pruned $DELETED backup(s) older than $RETENTION_DAYS days"

log "done"
