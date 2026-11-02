#!/usr/bin/env bash
# deploy-gate: the only command the GitHub deploy key may run.
# Usage: deploy <40-hex-sha>. Does not back up or roll back database data.
set -euo pipefail
REPO="${DG_REPO:-/root/crm-ai-assitant}"
CF=docker-compose.server.yml
IMG=crm-assistant-server-app
RTAG=crm-assistant-server-app:rollback
LOGDIR="${DG_LOGDIR:-/root/logs}"
LOG=$LOGDIR/deploy.log
LOCK=$LOGDIR/deploy.lock
ORIGIN="git@github.com:IbexDataIsrael/crm-ai-assitant.git"
MIN_FREE=2560
MEMF="${DG_MEMINFO:-/proc/meminfo}"
TRIES="${DG_TRIES:-12}"
GAP="${DG_GAP:-10}"
mkdir -p "$LOGDIR"
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }
fail() { say "FAIL (exit $2): $1"; printf 'deploy failed: %s\n' "$1" >&2; exit "$2"; }
trap 'rc=$?; say "FAILED rc=$rc at line $LINENO"; exit $rc' ERR

# only: deploy <40 hex>; raw command never logged
cmd="${SSH_ORIGINAL_COMMAND:-}"
case "$cmd" in deploy\ *) ;; *) fail "only 'deploy <sha>' accepted" 2 ;; esac
sha="${cmd#deploy }"
printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$' || fail "sha must be 40 lowercase hex" 2

# one deploy at a time; real repo; clean tracked tree
exec 9>"$LOCK"
flock -n 9 || fail "another deploy in progress" 3
cd "$REPO" || fail "repo missing" 4
[ "$(git remote get-url origin)" = "$ORIGIN" ] || fail "origin mismatch" 4
[ -z "$(git status --porcelain --untracked-files=no)" ] || fail "tracked files modified on server" 4

# .env untracked: local + target commit
git ls-files --error-unmatch .env >/dev/null 2>&1 && fail ".env tracked locally" 4
git fetch origin main >>"$LOG" 2>&1 || fail "git fetch failed" 6
git ls-tree --name-only "$sha" | grep -qx '.env' && fail "target commit contains .env" 5

# on main AND its tip
git merge-base --is-ancestor "$sha" origin/main || fail "not on main" 5
[ "$sha" = "$(git rev-parse origin/main)" ] || fail "not current main tip; older commit" 5

# RAM floor (baseline)
avail=$(awk '/MemAvailable/ {print int($2/1024)}' "$MEMF")
[ "$avail" -ge "$MIN_FREE" ] || fail "only ${avail}MiB free, need $MIN_FREE" 7

old="$(git rev-parse HEAD)"
ct="$(docker compose -f "$CF" ps -q app)"
[ -n "$ct" ] || fail "app container not running" 4
oimg="$(docker inspect -f '{{.Image}}' "$ct")"
docker tag "$oimg" "$RTAG" >>"$LOG" 2>&1
say "START $sha (was $old, ${avail}MiB free)"

healthy() {
  for i in $(seq 1 "$TRIES"); do
    sleep "$GAP"
    ct="$(docker compose -f "$CF" ps -q app 2>/dev/null || true)"
    [ -n "$ct" ] || continue
    run="$(docker inspect -f '{{.Image}}' "$ct" 2>/dev/null || true)"
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8020/health || true)"
    [ "$run" = "$1" ] && [ "$code" = 200 ] && return 0
  done
  return 1
}

rollback() {
  rc=0
  git reset --hard "$1" >>"$LOG" 2>&1 || { say "ROLLBACK git reset FAILED"; rc=1; }
  docker tag "$RTAG" "$IMG" >>"$LOG" 2>&1 || { say "ROLLBACK retag FAILED"; rc=1; }
  docker compose -f "$CF" up -d --no-build --no-deps --force-recreate app >>"$LOG" 2>&1 || { say "ROLLBACK recreate FAILED"; rc=1; }
  [ "$rc" -eq 0 ] || return 1
  healthy "$2"
}

# build BEFORE touching the running app
git reset --hard "$sha" >>"$LOG" 2>&1 || fail "git reset failed" 8
if ! docker compose -f "$CF" build app >>"$LOG" 2>&1; then
  git reset --hard "$old" >>"$LOG" 2>&1
  fail "build failed; running app never touched" 8
fi
nimg="$(docker image inspect -f '{{.Id}}' "$IMG")"

# apply to app only; db never recreated
if ! docker compose -f "$CF" up -d --no-build --no-deps app >>"$LOG" 2>&1; then
  say "apply failed for $sha; rolling back to $old"
  rollback "$old" "$oimg" || fail "ROLLBACK FAILED after apply failure - manual intervention NOW" 10
  fail "apply failed; rolled back to $old" 9
fi

# health-gate NEW image; else verified rollback
if healthy "$nimg"; then say "OK deployed $sha"; exit 0; fi
say "UNHEALTHY $sha; rolling back to $old"
rollback "$old" "$oimg" || fail "ROLLBACK FAILED or rollback unhealthy - manual intervention NOW" 10
say "ROLLED BACK to $old"
fail "deploy of $sha failed health; rolled back to $old" 9
