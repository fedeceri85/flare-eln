#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.local-stack.yml}"
BACKUP_DIR="${BACKUP_DIR:-backups/local-stack}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"
docker compose -f "$COMPOSE_FILE" up -d db

DB_NAME="$(
  docker compose -f "$COMPOSE_FILE" exec -T db sh -c 'printf "%s" "$POSTGRES_DB"'
)"
DB_USER="$(
  docker compose -f "$COMPOSE_FILE" exec -T db sh -c 'printf "%s" "$POSTGRES_USER"'
)"
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

docker compose -f "$COMPOSE_FILE" exec -T db \
  pg_dump -U "$DB_USER" -d "$DB_NAME" \
  | gzip > "$BACKUP_FILE"

echo "$BACKUP_FILE"
