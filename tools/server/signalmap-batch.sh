#!/bin/sh
# Forced command for the jiri-signalmap-batch SSH key.
#
# That key has no passphrase, so it must not be able to do anything dangerous.
# Its entry in /root/.ssh/authorized_keys pins it to this script:
#
#   restrict,command="/usr/local/bin/signalmap-batch.sh" ssh-ed25519 AAAA... jiri-signalmap-batch
#
# `restrict` disables the pty, port/agent forwarding and X11; `command=` means the
# server runs THIS script no matter what the client asked for. Whatever the client
# typed arrives in SSH_ORIGINAL_COMMAND and is matched against the whitelist below.
#
# THREE RULES when adding an operation:
#   1. Exact literals only. The single exception is backup-get-*, and it validates
#      the argument against a fixed pattern BEFORE using it — never interpolate
#      SSH_ORIGINAL_COMMAND into a command, that hands a shell back to whoever
#      holds the key.
#   2. Read-only. Anything that changes or deletes state belongs to the interactive
#      key with a passphrase, not here. Backup retention is the server's own job
#      (signalmap-backup.sh, running as root under cron).
#   3. This file is owned by root and not writable by anyone else — whoever can edit
#      it gains everything the key can do.
set -eu

BACKUP_DIR=${BACKUP_DIR:-/var/backups/signalmap}
# The public URL, not http://127.0.0.1 — Caddy serves a single site block for
# SITE_ADDRESS, so a request without that Host header gets a redirect instead of
# the app, and curl without -L then prints nothing and still exits 0.
HEALTH_URL=${HEALTH_URL:-https://expressyourself.ai/health}

logger -t signalmap-batch "request: ${SSH_ORIGINAL_COMMAND:-<none>}"

case "${SSH_ORIGINAL_COMMAND:-}" in
  backup-latest)
      f=$(ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | head -1)
      [ -n "$f" ] || { echo "no backup available" >&2; exit 1; }
      exec cat "$f" ;;
  backup-list)
      exec ls -1t "$BACKUP_DIR"/ ;;
  backup-get-*)
      # Lets the workstation fetch a specific dump, so a day when the PC was off
      # can still be collected later. The name is matched against a fixed pattern
      # first: no slashes, no dots-dots, nothing but a dump filename this server
      # produces itself.
      name=${SSH_ORIGINAL_COMMAND#backup-get-}
      case "$name" in
        signalmap-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9].dump) ;;
        *) echo "denied: '$name' is not a valid backup name" >&2; exit 1 ;;
      esac
      [ -f "$BACKUP_DIR/$name" ] || { echo "no such backup: $name" >&2; exit 1; }
      exec cat "$BACKUP_DIR/$name" ;;
  health)
      exec curl -fsS --max-time 15 "$HEALTH_URL" ;;
  disk-usage)
      exec df -h / /var/lib/docker ;;
  *)
      echo "denied: '${SSH_ORIGINAL_COMMAND:-}' is not an allowed operation" >&2
      exit 1 ;;
esac
