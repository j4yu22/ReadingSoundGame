#!/bin/bash
# Installed root-owned in /usr/local/bin; invoked by the existing systemd timer.
set -euo pipefail
umask 077

APP_DIR="${1:-/opt/reading-sound-game}"
REPOSITORY_REF="${2:-main}"
cd "$APP_DIR"

# A manual run and the timer must not change the checkout simultaneously.
lock_file="$(git rev-parse --git-path reading-sound-game-update.lock)"
exec 9>"$lock_file"
flock -n 9 || { echo "An update is already running."; exit 0; }
pending_file="$(git rev-parse --git-path reading-sound-game-deploy-pending)"

git fetch origin "$REPOSITORY_REF" --prune
current_commit="$(git rev-parse HEAD)"
if git rev-parse --verify "origin/$REPOSITORY_REF" >/dev/null 2>&1; then
  target_commit="$(git rev-parse "origin/$REPOSITORY_REF")"
else
  target_commit="$(git rev-parse 'FETCH_HEAD^{commit}')"
fi

if [ "$current_commit" = "$target_commit" ] && [ ! -f "$pending_file" ]; then
  echo "Already on latest commit $current_commit."
  exit 0
fi

# Retain this marker until the new app is healthy. Git HEAD alone cannot prove
# deployment succeeded: a previous image build may have failed after reset.
printf '%s\n' "$target_commit" > "$pending_file"
git checkout "$REPOSITORY_REF"
git reset --hard "$target_commit"
docker compose build app

stopped_for_cache=0
recover_cache_failure() {
  result=$?
  if [ "$stopped_for_cache" = "1" ]; then
    docker compose start app >/dev/null 2>&1 || true
  fi
  echo "Deployment failed; the next timer run will retry." >&2
  exit "$result"
}
trap recover_cache_failure ERR

# Stop only the older root app before migrating its volume. This prevents it
# creating new root-owned cache files between the chown and container replacement.
old_container="$(docker compose ps -q app)"
if [ -n "$old_container" ]; then
  old_user="$(docker inspect --format '{{.Config.User}}' "$old_container")"
  case "$old_user" in
    ""|root|0|root:*|0:*)
      docker compose stop app
      stopped_for_cache=1
      ;;
  esac
fi
docker compose run --rm --no-deps --user root --entrypoint chown app \
  -R 10001:10001 /app/src/api/.cache
stopped_for_cache=0

docker compose up -d --wait --wait-timeout 180
rm -f -- "$pending_file"
trap - ERR
docker image prune -f || true
echo "Deployment healthy at $target_commit."
