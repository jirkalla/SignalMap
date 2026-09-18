#!/bin/sh
# Install the host-side scripts on the server.
#
# Run from the deployed copy, as root:
#   cd /opt/signalmap && ./tools/server/install.sh
#
# Part of the deployment routine: run it after every `git reset --hard origin/master`
# so the host scripts stay in sync with the repository. That also means a change made
# directly on the server is overwritten here — the repository is the source of truth.
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
BACKUP_DIR=${BACKUP_DIR:-/var/backups/signalmap}
MAINT_DIR=${MAINT_DIR:-/var/lib/signalmap/maintenance}

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }

install -o root -g root -m 755 "$SRC/signalmap-backup.sh" /usr/local/bin/signalmap-backup.sh
install -o root -g root -m 755 "$SRC/signalmap-batch.sh"  /usr/local/bin/signalmap-batch.sh

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Readable by the caddy container's own user, unlike $BACKUP_DIR above —
# Caddy must be able to serve index.html and check for the maintenance.on
# flag on every request. The flag file itself is never touched here (see
# docs/TASKS_MAINTENANCE_PAGE.md, T1 point 4): toggling it is a manual
# runbook step, not part of install/deploy.
mkdir -p "$MAINT_DIR"
chmod 755 "$MAINT_DIR"
install -o root -g root -m 644 "$SRC/maintenance.html" "$MAINT_DIR/index.html"

echo "installed:"
ls -l /usr/local/bin/signalmap-backup.sh /usr/local/bin/signalmap-batch.sh
ls -ld "$BACKUP_DIR"
ls -ld "$MAINT_DIR"
ls -l "$MAINT_DIR/index.html"
echo
echo "cron is NOT installed automatically — see tools/server/crontab.example"
