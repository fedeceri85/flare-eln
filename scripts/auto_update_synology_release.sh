#!/usr/bin/env bash
# Install outside the application directory; run from DSM Task Scheduler.
set -euo pipefail
export PATH="${PATH:-}:/usr/local/bin:/usr/bin:/bin:/usr/syno/bin:/var/packages/ContainerManager/target/usr/bin:/var/packages/Docker/target/usr/bin"

PROJECT_DIR="${PROJECT_DIR:-/volume1/docker/flare-eln/project}"
STATE_DIR="${STATE_DIR:-/volume1/docker/flare-eln/automation}"
BACKUP_DIR="${BACKUP_DIR:-/volume1/docker/flare-eln/backups}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.synology.yml}"
: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME to the EXISTING Container Manager project name}"
export COMPOSE_PROJECT_NAME
# Only used to resolve Compose while inspecting the existing containers. It is
# replaced with the downloaded release digest before any run/up command.
export FLARE_IMAGE=ghcr.io/fedeceri85/flare-eln:bootstrap

mkdir -p "$STATE_DIR"
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
ENV_TEMP=""
cleanup() {
  local result=$?
  trap - EXIT
  if [ "$result" -ne 0 ] && [ "$DEPLOYING" = 1 ]; then
    echo "Deployment failed. Review update.log before resuming automation." > "$STATE_DIR/FAILED"
    echo "ERROR: deployment stopped; automatic retries are blocked by $STATE_DIR/FAILED."
  fi
  if [ -n "$ENV_TEMP" ]; then
    rm -f "$ENV_TEMP"
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

# Refuse the old build-from-source Compose file before touching any containers.
if ! compose config --images | grep -Fx "$FLARE_IMAGE" >/dev/null; then
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
  if [ "$(docker inspect --format '{{.State.Running}}' "$container_id")" != true ]; then
    echo "ERROR: existing web/db must be running before the scheduled update."
    exit 1
  fi
done

# Python is already installed in web; no Python/jq package is needed on DSM.
RELEASE="$(curl --fail --silent --show-error --location \
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
if [ -f "$STATE_DIR/deployed-release" ] && [ "$(cat "$STATE_DIR/deployed-release")" = "$RELEASE" ] \
  && [ -f "$STATE_DIR/deployed-image-id" ] \
  && [ "$(docker inspect --format '{{.Image}}' "$WEB_ID")" = "$(cat "$STATE_DIR/deployed-image-id")" ]; then
  echo "Already running $RELEASE_TAG. No update needed."
  exit 0
fi

TAG_IMAGE="ghcr.io/fedeceri85/flare-eln:$RELEASE_TAG"
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
printf '%s\n' "$FLARE_IMAGE" > "$STATE_DIR/candidate-image"
docker inspect --format '{{.Config.Image}}' "$WEB_ID" > "$STATE_DIR/previous-image"

echo "Checking the downloaded image before stopping the current website..."
compose run --rm --no-deps -T web python manage.py check

echo "Stopping web briefly to back up and migrate without web writes..."
DEPLOYING=1
compose stop web
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/pre_${RELEASE_TAG}_$(date +%Y%m%d_%H%M%S)_$$.sql.gz"
compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | gzip > "$BACKUP_FILE.part"
gzip -t "$BACKUP_FILE.part"
mv "$BACKUP_FILE.part" "$BACKUP_FILE"
printf '%s\n' "$BACKUP_FILE" > "$STATE_DIR/latest-backup"
echo "Database backup: $BACKUP_FILE"

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
