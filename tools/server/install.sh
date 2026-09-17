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

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }

install -o root -g root -m 755 "$SRC/signalmap-backup.sh" /usr/local/bin/signalmap-backup.sh
install -o root -g root -m 755 "$SRC/signalmap-batch.sh"  /usr/local/bin/signalmap-batch.sh

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "installed:"
ls -l /usr/local/bin/signalmap-backup.sh /usr/local/bin/signalmap-batch.sh
ls -ld "$BACKUP_DIR"
echo
echo "cron is NOT installed automatically — see tools/server/crontab.example"
