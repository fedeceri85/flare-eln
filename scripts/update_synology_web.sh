#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.synology.yml}"
BACKUP_DIR="${BACKUP_DIR:-/volume1/docker/flare-eln/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

docker compose -f "$COMPOSE_FILE" up -d db

echo "Creating backup before update..."
mkdir -p "$BACKUP_DIR"
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

echo "Backup created: $BACKUP_FILE"

echo "Building web image..."
docker compose -f "$COMPOSE_FILE" build web

echo "Running migrations..."
docker compose -f "$COMPOSE_FILE" run --rm web python manage.py migrate --noinput

echo "Starting updated web container..."
docker compose -f "$COMPOSE_FILE" up -d web

echo "Running Django system check..."
docker compose -f "$COMPOSE_FILE" run --rm web python manage.py check

docker compose -f "$COMPOSE_FILE" ps
