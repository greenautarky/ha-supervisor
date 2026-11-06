#!/usr/bin/env bash
# Start the built Supervisor image next to a throwaway docker-in-docker daemon
# and require it to STAY UP for HOLD seconds and its API port to answer.
# Usage: scripts/start-test.sh <image> [platform]
# Fails closed: any exit of the container, any add-on-base boot marker, or no
# answer on port 80 is a failure. (2026.10.5.1 exited after ~7 s: s6 ran the
# add-on oneshots base-addon-banner / base-addon-log-level.)
set -uo pipefail
IMAGE="${1:?image}"
PLATFORM="${2:-}"
HOLD="${HOLD:-60}"
id=$$
sup="sup-start-test-$id"; dind="sup-dind-$id"; vol="sup-dsock-$id"
cleanup() { docker rm -f "$sup" "$dind" >/dev/null 2>&1; docker volume rm "$vol" "$vol-data" >/dev/null 2>&1; }
trap cleanup EXIT
docker volume create "$vol" >/dev/null || exit 1
docker run -d --privileged --name "$dind" -v "$vol":/var/run docker:dind >/dev/null || exit 1
for i in $(seq 1 60); do docker exec "$dind" docker info >/dev/null 2>&1 && break; sleep 1; done
docker exec "$dind" docker info >/dev/null 2>&1 || { echo "FAIL: dind did not come up"; exit 1; }
docker run -d --name "$sup" ${PLATFORM:+--platform "$PLATFORM"} \
  -v "$vol":/var/run -v "$vol-data":/data \
  -e SUPERVISOR_SHARE=/tmp/ha -e SUPERVISOR_NAME=hassio_supervisor \
  -e SUPERVISOR_MACHINE=qemuarm -e HOMEASSISTANT_REPOSITORY=ghcr.io/home-assistant/qemuarm-homeassistant \
  "$IMAGE" >/dev/null || exit 1
for i in $(seq 1 "$HOLD"); do
  st=$(docker inspect -f '{{.State.Status}}' "$sup" 2>/dev/null || echo gone)
  if [ "$st" != running ]; then
    echo "FAIL: container left 'running' after ${i}s (state: $st, exit $(docker inspect -f '{{.State.ExitCode}}' "$sup" 2>/dev/null))"
    docker logs "$sup" 2>&1 | grep -vE '^\s+(File|\^|~)' | tail -25
    exit 1
  fi
  sleep 1
done
logs=$(docker logs "$sup" 2>&1)
if echo "$logs" | grep -qE 'Failed to get addon config|Unknown log_level|fatal: stopping the container'; then
  echo "FAIL: add-on base boot-failure markers in log"; echo "$logs" | tail -25; exit 1
fi
code=$(docker exec "$sup" curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost/ 2>/dev/null || true)
if [ -z "$code" ] || [ "$code" = 000 ]; then
  echo "FAIL: API port 80 did not answer (curl: '$code')"; echo "$logs" | tail -25; exit 1
fi
echo "OK: $IMAGE stayed up ${HOLD}s, API port 80 answered HTTP $code"
