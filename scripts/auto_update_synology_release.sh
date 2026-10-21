#!/usr/bin/env bash
# Install outside the application directory; run from DSM Task Scheduler.
set -euo pipefail
umask 077
ulimit -c 0
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/syno/bin:/var/packages/ContainerManager/target/usr/bin:/var/packages/Docker/target/usr/bin"
unset CDPATH ENV BASH_ENV DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
export DOCKER_HOST=unix:///var/run/docker.sock

DEPLOY_ROOT="${DEPLOY_ROOT:-/volume1/docker/flare-eln}"
PROJECT_DIR="${PROJECT_DIR:-$DEPLOY_ROOT/project}"
STATE_DIR="${STATE_DIR:-$DEPLOY_ROOT/automation}"
BACKUP_DIR="${BACKUP_DIR:-$DEPLOY_ROOT/backups}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.synology.yml}"
MIN_FREE_MB="${MIN_FREE_MB:-2048}"
: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME to the EXISTING Container Manager project name}"
export COMPOSE_PROJECT_NAME

fail() { echo "ERROR: $*" >&2; exit 1; }
case "${1:-}" in
  ''|--check-only) ;;
  *) fail "Usage: $0 [--check-only]" ;;
esac
[[ "$MIN_FREE_MB" =~ ^[0-9]{1,6}$ ]] && (( 10#$MIN_FREE_MB > 0 )) || fail "MIN_FREE_MB must be a positive integer."
available_kb() {
  df -Pk "$1" | awk 'NR > 1 { available = $4 } END { if (available ~ /^[0-9]+$/) print available; else exit 1 }'
}
require_space() {
  local free_kb
  free_kb="$(available_kb "$1")"
  (( free_kb >= 10#$MIN_FREE_MB * 1024 )) || fail "Less than $MIN_FREE_MB MB free at $1; refusing deployment."
}
safe_path() {
  case "$1" in
    /*) ;;
    *) fail "Deployment paths must be absolute: $1" ;;
  esac
  case "$1/" in
    *'/../'*|*'/./'*|*'//'*) fail "Use a path without dot components or repeated slashes: $1" ;;
  esac
}
protected_path() {
  local metadata owner mode links
  [ ! -L "$1" ] || fail "Refusing symbolic link: $1"
  metadata="$(stat -c '%u %a %h' "$1" 2>/dev/null)" || metadata="$(stat -f '%u %Lp %l' "$1")"
  read -r owner mode links <<< "$metadata"
  [[ "$owner" = 0 || "$owner" = "$(id -u)" ]] || fail "File/directory must belong to the task user (root on NAS): $1"
  (( (8#$mode & 8#22) == 0 )) || fail "Group/other write permission must be removed: $1"
  if [ -f "$1" ] && [ "$links" != 1 ]; then
    fail "Refusing a file with multiple hard links: $1"
  fi
}
for path in "$DEPLOY_ROOT" "$PROJECT_DIR" "$STATE_DIR" "$BACKUP_DIR"; do
  safe_path "$path"
done
case "$DEPLOY_ROOT" in
  /|/etc|/etc/*|/usr|/usr/*|/bin|/bin/*|/sbin|/sbin/*|/dev|/dev/*|/proc|/proc/*|/sys|/sys/*|/boot|/boot/*)
    fail "DEPLOY_ROOT must be an application data directory, not a system directory." ;;
esac
for path in "$PROJECT_DIR" "$STATE_DIR" "$BACKUP_DIR"; do
  [[ "$path" = "$DEPLOY_ROOT/"* ]] || fail "All deployment folders must be inside DEPLOY_ROOT."
done
for pair in "$PROJECT_DIR|$STATE_DIR" "$PROJECT_DIR|$BACKUP_DIR" "$STATE_DIR|$BACKUP_DIR"; do
  first="${pair%%|*}"; second="${pair#*|}"
  [[ "$first/" != "$second/"* && "$second/" != "$first/"* ]] || fail "Project, automation, and backup folders must not overlap."
done
[[ "$COMPOSE_FILE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$ ]] || fail "COMPOSE_FILE must be a YAML filename inside PROJECT_DIR."
[[ "$COMPOSE_PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail "Invalid Compose project name."
[ -d "$DEPLOY_ROOT" ] && [ -d "$PROJECT_DIR" ] || fail "Existing deployment root and project folder are required."
for path in "$DEPLOY_ROOT" "$PROJECT_DIR"; do
  protected_path "$path"
  [ "$(cd "$path" && pwd -P)" = "$path" ] || fail "Deployment paths must not traverse symbolic links: $path"
done
for path in "$STATE_DIR" "$BACKUP_DIR"; do
  [ ! -L "$path" ] || fail "Refusing symbolic link: $path"
  if [ ! -d "$path" ]; then
    [ "${path%/*}" = "$DEPLOY_ROOT" ] || fail "Create and protect the configured directory first: $path"
    mkdir -m 700 "$path"
  fi
  protected_path "$path"
  [ "$(cd "$path" && pwd -P)" = "$path" ] || fail "Deployment paths must not traverse symbolic links: $path"
done
for path in "$PROJECT_DIR/.env" "$PROJECT_DIR/$COMPOSE_FILE" "$0"; do
  [ -f "$path" ] || fail "Required regular file missing: $path"
  protected_path "$path"
done
for name in update.log update.lock FAILED candidate-image previous-image latest-backup deployed-release deployed-release.tmp deployed-image deployed-image-id; do
  path="$STATE_DIR/$name"
  [ ! -L "$path" ] || fail "Refusing symbolic link: $path"
  if [ -e "$path" ]; then
    protected_path "$path"
    if [ "$name" != update.lock ]; then
      [ -f "$path" ] || fail "Expected a regular state file: $path"
    fi
  fi
done
# Only used to resolve Compose while inspecting the existing containers. It is
# replaced with the downloaded release digest before any run/up command.
export FLARE_IMAGE=ghcr.io/fedeceri85/flare-eln:bootstrap

if [ "${FLARE_UPDATE_LOGGING:-0}" != 1 ]; then
  FLARE_UPDATE_LOGGING=1 bash "$0" "$@" 2>&1 | tee -a "$STATE_DIR/update.log"
  exit 0
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] Checking GitHub releases..."
if ! mkdir "$STATE_DIR/update.lock" 2>/dev/null; then
  echo "Update lock exists. Another update may be running; see the guide before removing it."
  exit 1
fi
echo "$$" > "$STATE_DIR/update.lock/pid"
DEPLOYING=0
MIGRATIONS_STARTED=0
ENV_TEMP=""
BACKUP_TEMP=""
cleanup() {
  local result=$?
  trap - EXIT
  if [ "$result" -ne 0 ] && [ "$DEPLOYING" = 1 ]; then
    if [ "$MIGRATIONS_STARTED" = 0 ]; then
      echo "No migration started; restarting the original web container."
      docker start "$WEB_ID" >/dev/null || echo "ERROR: could not restart original web container."
    fi
    echo "Deployment failed. Review update.log before resuming automation." > "$STATE_DIR/FAILED"
    echo "ERROR: deployment stopped; automatic retries are blocked by $STATE_DIR/FAILED."
  fi
  if [ -n "$ENV_TEMP" ]; then
    rm -f "$ENV_TEMP"
  fi
  if [ -n "$BACKUP_TEMP" ]; then
    rm -f "$BACKUP_TEMP"
  fi
  rm -f "$STATE_DIR/update.lock/pid"
  rmdir "$STATE_DIR/update.lock"
  exit "$result"
}
trap cleanup EXIT
if [ -f "$STATE_DIR/FAILED" ]; then
  echo "ERROR: previous deployment failed. Read $STATE_DIR/update.log and the recovery guide."
  exit 1
fi

cd "$PROJECT_DIR"
if [ ! -f .env ] || [ ! -f "$COMPOSE_FILE" ]; then
  echo "ERROR: production .env or Compose file not found in $PROJECT_DIR."
  exit 1
fi
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: Docker Compose is not available."
  exit 1
fi
compose() { "${COMPOSE[@]}" --env-file "$PROJECT_DIR/.env" -f "$COMPOSE_FILE" "$@"; }

# Older Synology Compose releases lack `config --images`. Validate first, then
# inspect only web's image/build settings without printing resolved secrets.
if ! compose config -q; then
  echo "ERROR: Compose configuration is invalid; review the validation error above."
  exit 1
fi
if ! compose config | awk -v expected="$FLARE_IMAGE" '
  /^services:$/ { services = 1; next }
  /^[^[:space:]]/ { services = 0 }
  services && /^  [^[:space:]]/ { web = ($0 == "  web:"); next }
  services && web && /^    build:/ { build = 1 }
  services && web && /^    image:/ {
    image = $0
    sub(/^    image:[[:space:]]*/, "", image)
    gsub(/["\047]/, "", image)
    found = (image == expected)
  }
  END { exit !(found && !build) }
'; then
  echo "ERROR: install compose.synology.images.yml as the existing NAS Compose filename first."
  exit 1
fi
WEB_ID="$(compose ps -q web)"
DB_ID="$(compose ps -q db)"
if [ -z "$WEB_ID" ] || [ -z "$DB_ID" ]; then
  echo "ERROR: existing web/db containers not found. Check path and COMPOSE_PROJECT_NAME."
  exit 1
fi
for container_id in "$WEB_ID" "$DB_ID"; do
  [ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$container_id")" = "$COMPOSE_PROJECT_NAME" ] \
    || fail "Container does not belong to the configured Compose project."
  working_dir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$container_id")"
  if [ -n "$working_dir" ] && [ "$working_dir" != '<no value>' ] && [ "$working_dir" != "$PROJECT_DIR" ]; then
    fail "Existing containers belong to a different project directory: $working_dir"
  fi
  if [ "$(docker inspect --format '{{.State.Running}}' "$container_id")" != true ]; then
    echo "ERROR: existing web/db must be running before the scheduled update."
    exit 1
  fi
done
if [ "${1:-}" = --check-only ]; then
  echo "Preflight checks passed. No image download, container stop, or migration was performed."
  exit 0
fi

# Python is already installed in web; no Python/jq package is needed on DSM.
RELEASE="$(curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --connect-timeout 15 --max-time 60 --retry 2 \
  -H 'Accept: application/vnd.github+json' \
  https://api.github.com/repos/fedeceri85/flare-eln/releases/latest \
  | docker exec -i "$WEB_ID" python -c '
import json, re, sys
release = json.load(sys.stdin)
tag = release["tag_name"]
if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tag):
    raise SystemExit("Expected a published stable release with a simple version tag such as v1.2.3")
print(str(int(release["id"])) + " " + tag)
')"
read -r RELEASE_ID RELEASE_TAG <<< "$RELEASE"
[[ "$RELEASE_ID" =~ ^[0-9]+$ && "$RELEASE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "Invalid release metadata returned by the container."
if [ -f "$STATE_DIR/deployed-release" ] && [ "$(cat "$STATE_DIR/deployed-release")" = "$RELEASE" ] \
  && [ -f "$STATE_DIR/deployed-image-id" ] \
  && [ "$(docker inspect --format '{{.Image}}' "$WEB_ID")" = "$(cat "$STATE_DIR/deployed-image-id")" ]; then
  echo "Already running $RELEASE_TAG. No update needed."
  exit 0
fi

TAG_IMAGE="ghcr.io/fedeceri85/flare-eln:$RELEASE_TAG"
DOCKER_STORAGE="$(docker info --format '{{.DockerRootDir}}')"
[ -d "$DOCKER_STORAGE" ] || fail "Cannot identify the local Docker storage directory."
require_space "$DOCKER_STORAGE"
require_space "$BACKUP_DIR"
echo "Downloading $TAG_IMAGE while the current website keeps running..."
if ! docker pull "$TAG_IMAGE"; then
  echo "Image unavailable: the GitHub build may still be running, the package may be private, or a network error occurred."
  echo "The website is unchanged. The next scheduled check will retry."
  exit 1
fi
IMAGE_VERSION="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$TAG_IMAGE")"
if [ "$IMAGE_VERSION" != "$RELEASE_TAG" ]; then
  echo "ERROR: image version label does not match $RELEASE_TAG. Website unchanged."
  exit 1
fi
# Pin the downloaded content, so migrations and the web service use one image.
FLARE_IMAGE="$(docker image inspect --format '{{json .RepoDigests}}' "$TAG_IMAGE" \
  | docker exec -i "$WEB_ID" python -c '
import json, re, sys
for digest in json.load(sys.stdin):
    if re.fullmatch(r"ghcr.io/fedeceri85/flare-eln@sha256:[a-f0-9]{64}", digest):
        print(digest)
        break
else:
    raise SystemExit("Downloaded image has no matching GHCR digest")
')"
export FLARE_IMAGE
[[ "$FLARE_IMAGE" =~ ^ghcr\.io/fedeceri85/flare-eln@sha256:[a-f0-9]{64}$ ]] || fail "Invalid image digest returned by the container."
printf '%s\n' "$FLARE_IMAGE" > "$STATE_DIR/candidate-image"
docker inspect --format '{{.Config.Image}}' "$WEB_ID" > "$STATE_DIR/previous-image"

echo "Checking the downloaded image before stopping the current website..."
compose run --rm --no-deps -T web python manage.py check
require_space "$BACKUP_DIR"
FREE_BACKUP_KB="$(available_kb "$BACKUP_DIR")"
# Bash file-size limits are in 1024-byte blocks (512 in POSIX mode). Halving
# the free budget is conservative in either case and keeps a disk reserve.
MAX_BACKUP_BLOCKS="$(( (FREE_BACKUP_KB - 10#$MIN_FREE_MB * 1024) / 2 ))"
(( MAX_BACKUP_BLOCKS > 0 )) || fail "Insufficient space for a bounded backup."

echo "Stopping web briefly to back up and migrate without web writes..."
DEPLOYING=1
compose stop web
BACKUP_FILE="$BACKUP_DIR/pre_${RELEASE_TAG}_$(date +%Y%m%d_%H%M%S)_$$.sql.gz"
BACKUP_TEMP="$(mktemp "$BACKUP_DIR/.backup.XXXXXX")"
compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | (ulimit -f "$MAX_BACKUP_BLOCKS"; gzip > "$BACKUP_TEMP")
gzip -t "$BACKUP_TEMP"
[ ! -e "$BACKUP_FILE" ] && [ ! -L "$BACKUP_FILE" ] || fail "Backup destination already exists."
mv "$BACKUP_TEMP" "$BACKUP_FILE"
BACKUP_TEMP=""
printf '%s\n' "$BACKUP_FILE" > "$STATE_DIR/latest-backup"
echo "Database backup: $BACKUP_FILE"

MIGRATIONS_STARTED=1
compose run --rm --no-deps -T web python manage.py migrate --noinput
compose up -d --no-deps --no-build web
echo "Checking the new running application and database..."
compose exec -T web python -c '
import os, time, urllib.request
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "labdb.settings")
import django
django.setup()
from django.db import connection
connection.ensure_connection()
host = os.environ["DJANGO_ALLOWED_HOSTS"].split(",")[0].strip()
if host == "*" or host.startswith("."):
    host = "localhost" if host == "*" else host[1:]
for attempt in range(30):
    try:
        request = urllib.request.Request("http://127.0.0.1:8000/login/", headers={"Host": host})
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("Unexpected HTTP status: " + str(response.status))
        break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(2)
print("Application HTTP and database checks passed.")
'
# Persist the exact image for Container Manager/manual restarts. Preserve all
# other configuration and keep the replacement file private.
ENV_TEMP="$(mktemp "$PROJECT_DIR/.env.image-update.XXXXXX")"
chmod 600 "$ENV_TEMP"
awk -v image="$FLARE_IMAGE" '
  /^[[:space:]]*(export[[:space:]]+)?FLARE_IMAGE[[:space:]]*=/ { next }
  { print }
  END { print "FLARE_IMAGE=" image }
' .env > "$ENV_TEMP"
mv "$ENV_TEMP" .env
ENV_TEMP=""
printf '%s\n' "$RELEASE" > "$STATE_DIR/deployed-release.tmp"
mv "$STATE_DIR/deployed-release.tmp" "$STATE_DIR/deployed-release"
printf '%s\n' "$FLARE_IMAGE" > "$STATE_DIR/deployed-image"
docker inspect --format '{{.Image}}' "$(compose ps -q web)" > "$STATE_DIR/deployed-image-id"
DEPLOYING=0
echo "SUCCESS: deployed $RELEASE_TAG ($FLARE_IMAGE)."
