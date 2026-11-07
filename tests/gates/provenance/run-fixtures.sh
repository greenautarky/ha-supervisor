#!/usr/bin/env bash
# Runs scripts/provenance-check.sh (the LIVE script) over must_fail + must_pass.
# Each fixture: {"expect":{"revision","mode"},"labels":{...}}. Expectations are
# pinned in the fixture, never derived from the labels. Also statically checks
# that the build workflow really calls the gate and stamps github.sha.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../../.." && pwd)
gate="$root/scripts/provenance-check.sh"; wf="$root/.github/workflows/build-ga-supervisor.yml"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
fail=0; nf=0; np=0
run() { # fixture -> rc
  jq -c .labels "$1" > "$tmp/l.json"
  "$gate" --expect-revision "$(jq -r .expect.revision "$1")" --expect-mode "$(jq -r .expect.mode "$1")" \
    --labels-file "$tmp/l.json" >"$tmp/out" 2>&1 && return 0 || return $?
}
for f in "$here"/must_fail/*.json; do
  nf=$((nf+1)); rc=0; run "$f" || rc=$?
  if [ "$rc" -eq 1 ]; then echo "ok   must_fail $(basename "$f"): $(head -1 "$tmp/out")"
  else echo "BAD  must_fail $(basename "$f") rc=$rc (expected 1)"; cat "$tmp/out"; fail=1; fi
done
for f in "$here"/must_pass/*.json; do
  np=$((np+1)); rc=0; run "$f" || rc=$?
  if [ "$rc" -eq 0 ]; then echo "ok   must_pass $(basename "$f")"
  else echo "BAD  must_pass $(basename "$f") rc=$rc"; cat "$tmp/out"; fail=1; fi
done
[ "$nf" -ge 5 ] && [ "$np" -ge 2 ] || { echo "BAD: too few fixtures (fail=$nf pass=$np)"; fail=1; }
grep -q 'scripts/provenance-check.sh' "$wf" || { echo "BAD: build workflow does not call scripts/provenance-check.sh"; fail=1; }
grep -q 'org.opencontainers.image.revision=${{ github.sha }}' "$wf" || { echo "BAD: build workflow does not stamp revision=github.sha"; fail=1; }
grep -Eq 'build-mode=DEV[[:space:]]*$' "$wf" && { echo "BAD: build-mode hard-coded DEV in workflow"; fail=1; }
# the gate step must come before the push step
g=$(grep -n 'provenance-check.sh' "$wf" | head -1 | cut -d: -f1); p=$(grep -n 'name: Push DEV tags' "$wf" | head -1 | cut -d: -f1)
[ -n "$g" ] && [ -n "$p" ] && [ "$g" -lt "$p" ] || { echo "BAD: provenance gate is not before the push step"; fail=1; }
[ "$fail" -eq 0 ] && echo "ALL OK ($nf must_fail, $np must_pass)"
exit "$fail"
