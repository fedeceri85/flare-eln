#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 backups/local-stack/<backup-file>.sql.gz" >&2
  exit 2
fi

BACKUP_FILE="$1"
COMPOSE_FILE="${COMPOSE_FILE:-compose.local-stack.yml}"
RESTORE_DB_NAME="${RESTORE_DB_NAME:-lab_container_stack_restore}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

docker compose -f "$COMPOSE_FILE" up -d db

DB_USER="$(
  docker compose -f "$COMPOSE_FILE" exec -T db sh -c 'printf "%s" "$POSTGRES_USER"'
)"

docker compose -f "$COMPOSE_FILE" exec -T db \
  dropdb -U "$DB_USER" --if-exists "$RESTORE_DB_NAME"

docker compose -f "$COMPOSE_FILE" exec -T db \
  createdb -U "$DB_USER" "$RESTORE_DB_NAME"

gunzip -c "$BACKUP_FILE" \
  | docker compose -f "$COMPOSE_FILE" exec -T db \
      psql -U "$DB_USER" -d "$RESTORE_DB_NAME" -v ON_ERROR_STOP=1

echo "Restored $BACKUP_FILE into database $RESTORE_DB_NAME"
