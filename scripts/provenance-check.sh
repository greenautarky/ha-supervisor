#!/usr/bin/env bash
# Provenance gate: the built image must carry labels that tie it to THIS build.
# Usage: scripts/provenance-check.sh --expect-revision <sha> --expect-mode <DEV|RELEASE> \
#          [--expect-source <url>] (--image <ref> | --labels-file <json>)
# --labels-file holds the label map ({"k":"v",...}) as `docker inspect` prints
# .Config.Labels. Fails closed: no labels at all is a failure, not a skip.
# Public file (no secrets). OS-side twin: proposals/check-supervisor-image-provenance.patch.
set -euo pipefail
rev="" mode="" src="" image="" lf=""
while [ $# -gt 0 ]; do
  case "$1" in
    --expect-revision) rev="$2"; shift 2 ;;
    --expect-mode) mode="$2"; shift 2 ;;
    --expect-source) src="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    --labels-file) lf="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ "$rev" =~ ^[0-9a-f]{40}$ ]] || { echo "FAIL: --expect-revision must be a full 40-hex sha (got '${rev}')"; exit 2; }
case "$mode" in DEV|RELEASE) ;; *) echo "FAIL: --expect-mode must be DEV or RELEASE (got '${mode}')"; exit 2 ;; esac
if [ -n "$image" ]; then
  labels=$(docker image inspect --format '{{json .Config.Labels}}' "$image")
elif [ -n "$lf" ]; then
  labels=$(cat "$lf")
else
  echo "FAIL: need --image or --labels-file" >&2; exit 2
fi
n=$(jq 'if type=="object" then length else 0 end' <<<"$labels")
[ "$n" -ge 1 ] || { echo "FAIL: no labels found (inspected nothing)"; exit 1; }
get() { jq -r --arg k "$1" '.[$k] // ""' <<<"$labels"; }
fail=0
a=$(get org.opencontainers.image.revision)
if [ -z "$a" ]; then
  echo "FAIL: org.opencontainers.image.revision is missing"; fail=1
elif [ "$a" != "$rev" ]; then
  echo "FAIL: revision label '$a' != build commit '$rev' (inherited from the base image?)"; fail=1
fi
m=$(get org.greenautarky.build-mode)
if [ "$m" != "$mode" ]; then
  echo "FAIL: org.greenautarky.build-mode '${m:-<missing>}' != expected '$mode'"; fail=1
fi
[ -n "$(get org.opencontainers.image.created)" ] || { echo "FAIL: org.opencontainers.image.created is missing"; fail=1; }
s=$(get org.opencontainers.image.source)
if [ -z "$s" ]; then echo "FAIL: org.opencontainers.image.source is missing"; fail=1
elif [ -n "$src" ] && [ "$s" != "$src" ]; then echo "FAIL: source '$s' != '$src'"; fail=1; fi
d=$(get org.greenautarky.base-digest)
[[ "$d" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "FAIL: org.greenautarky.base-digest missing or not a sha256 digest ('${d}')"; fail=1; }
[ "$fail" -eq 0 ] && echo "OK: provenance labels tie the image to $rev (mode $mode, $n labels inspected)"
exit "$fail"
