#!/usr/bin/env bash
# Static check: the image is a SUPERVISOR, not an add-on base.
# Usage: scripts/image-layout-check.sh <image> [platform]
# Needs no execution of the image (works for foreign architectures).
# Fails closed: an image where the expected anchors are not found is a failure.
set -euo pipefail
IMAGE="${1:?image}"
PLATFORM="${2:-}"
c=$(docker create ${PLATFORM:+--platform "$PLATFORM"} "$IMAGE" x)
trap 'docker rm -f "$c" >/dev/null 2>&1 || true' EXIT
list=$(mktemp); trap 'rm -f "$list"; docker rm -f "$c" >/dev/null 2>&1 || true' EXIT
docker export "$c" | tar -t > "$list"
fail=0
# anchors: what a Supervisor image must have, and proof the listing is real
for must in etc/services.d/supervisor/run etc/services.d/watchdog/run; do
  grep -qx "$must" "$list" || { echo "FAIL: missing $must"; fail=1; }
done
n=$(grep -c 'etc/s6-rc/sources/fix-attrs/$' "$list" || true)
[ "$n" -ge 1 ] || { echo "FAIL: no s6-rc sources found (inspected nothing)"; fail=1; }
# add-on boot oneshots must not exist anywhere in the s6 trees
bad=$(grep -E '(s6-rc(\.d)?/|s6-rc/sources/|s6-rc/scripts/).*base-addon' "$list" || true)
if [ -n "$bad" ]; then
  echo "FAIL: add-on base oneshots present (this is an add-on base, not a Supervisor):"
  echo "$bad" | head -20
  fail=1
fi
[ "$fail" -eq 0 ] && echo "OK: $IMAGE has a Supervisor s6 layout ($(wc -l < "$list") entries inspected)"
exit "$fail"
