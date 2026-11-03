#!/bin/sh
# Run as root on the confirmed VPS after the previous bot owner has handed off.
set -eu
image=${1:-qulay-media-bot:0.4.4}
env_file=${2:?Pass the absolute path of the private VPS environment file}
case "$env_file" in /*) ;; *) echo "Environment path must be absolute" >&2; exit 1;; esac
test -f "$env_file"
if docker container inspect qulay-media-bot >/dev/null 2>&1; then
    echo "A bot container already exists. Inspect it before replacing it." >&2
    exit 1
fi
docker image inspect "$image" >/dev/null
docker volume create qulay-telegram-api >/dev/null
docker run --rm --user 0 --entrypoint /bin/sh \
    --mount type=volume,src=qulay-telegram-api,dst=/home/appuser/telegram-api \
    "$image" -c 'chown appuser:appuser /home/appuser/telegram-api'
docker run -d --name qulay-media-bot --init --restart unless-stopped \
    --stop-timeout 120 --memory 3g --cpus 3 \
    --env-file "$env_file" \
    --publish 127.0.0.1:8080:8080 \
    --mount type=volume,src=qulay-telegram-api,dst=/home/appuser/telegram-api \
    --log-opt max-size=10m --log-opt max-file=3 \
    --health-cmd 'python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8080/health\", timeout=5)"' \
    --health-interval 30s --health-timeout 8s --health-start-period 90s --health-retries 3 \
    "$image"
echo "Bot started. Check localhost:8080/health and the container logs."
