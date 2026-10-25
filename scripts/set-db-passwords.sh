#!/usr/bin/env bash
# Database passwords for the server profile (docker-compose.server.yml). Linux.
#
# First time (before the first "up"):
#   ./scripts/set-db-passwords.sh
# Generates three random passwords - crm_app (the app), crm_test (the tests)
# and the admin (crm_assistant, used only when the volume is first created) -
# and writes them into .env. The empty database volume picks them up on first
# boot (scripts/db/init/10-roles.sh). Refuses if .env already holds any of
# them, or if the server database volume already exists.
#
# Change the crm_app / crm_test passwords later (containers running):
#   ./scripts/set-db-passwords.sh --rotate
# Sets new passwords in PostgreSQL first, then in .env, then recreates the app.
# The admin password is left as it is (it is only read on first boot).
#
# Options: --env-file PATH (default: .env in the project folder).
#
# Nothing is printed except what happened: no password is ever displayed,
# logged or put on a command line. .env is plain text with mode 600 - readable
# by this user and root only. Values are written in single quotes; the
# generated ones use only A-Z a-z 0-9.

set -euo pipefail
umask 077

project_root="$(cd "$(dirname "$0")/.." && pwd)"
compose_file="$project_root/docker-compose.server.yml"
sql_file="$project_root/scripts/db/set-passwords.sql"
volume_name="crm-assistant-server_pgdata"  # project name + volume in docker-compose.server.yml
env_file="$project_root/.env"
rotate=false

while [ $# -gt 0 ]; do
    case "$1" in
        --rotate) rotate=true ;;
        --env-file) shift; env_file="${1:?--env-file needs a path}" ;;
        -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (use --rotate, --env-file PATH)" >&2; exit 2 ;;
    esac
    shift
done

new_password() {
    # 32 characters from 62 (about 190 random bits). tr keeps only the
    # alphabet; 256 random bytes leave about 62 characters, so retry if short.
    local value=""
    while [ ${#value} -lt 32 ]; do
        value="$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9')"
    done
    printf '%s' "${value:0:32}"
}

has_value() {  # has_value NAME: .env has a non-empty NAME= line
    [ -f "$env_file" ] && grep -Eq "^[[:space:]]*$1[[:space:]]*=[[:space:]]*['\"]?[^'\"[:space:]]" "$env_file"
}

write_env() {  # write_env NAME... : replace those lines in .env, keep every other line
    local tmp
    tmp="$(mktemp "$(dirname "$env_file")/.env.tmp.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    if [ -f "$env_file" ]; then
        local pattern
        pattern="^[[:space:]]*($(IFS='|'; echo "$*"))[[:space:]]*="
        grep -Ev "$pattern" "$env_file" > "$tmp" || true
    fi
    local name
    for name in "$@"; do
        printf "%s='%s'\n" "$name" "${!name}" >> "$tmp"
    done
    chmod 600 "$tmp"
    mv "$tmp" "$env_file"
    trap - EXIT
}

compose() {
    docker compose -f "$compose_file" --env-file "$env_file" "$@"
}

if ! $rotate; then
    for name in APP_DB_PASSWORD TEST_DB_PASSWORD DB_ADMIN_PASSWORD; do
        if has_value "$name"; then
            echo "$name is already set in $env_file - nothing changed." >&2
            echo "To change the app and test passwords, run: $0 --rotate" >&2
            exit 1
        fi
    done
    if docker volume inspect "$volume_name" >/dev/null 2>&1; then
        echo "The database volume $volume_name already exists, so its passwords are already set" >&2
        echo "and new ones in .env would not match - nothing changed. Use --rotate with the" >&2
        echo "containers running (or restore the .env that belongs to this volume)." >&2
        exit 1
    fi
    APP_DB_PASSWORD="$(new_password)"
    TEST_DB_PASSWORD="$(new_password)"
    DB_ADMIN_PASSWORD="$(new_password)"
    write_env APP_DB_PASSWORD TEST_DB_PASSWORD DB_ADMIN_PASSWORD
    unset APP_DB_PASSWORD TEST_DB_PASSWORD DB_ADMIN_PASSWORD
    echo "Wrote 3 random passwords to $env_file (mode 600, not displayed)."
    echo "Next: set CRM_API_URL in it, then: docker compose -f docker-compose.server.yml up -d --build"
    exit 0
fi

# --rotate
if ! has_value APP_DB_PASSWORD || ! has_value DB_ADMIN_PASSWORD; then
    echo "No existing passwords in $env_file - run without --rotate first." >&2
    exit 1
fi
if ! compose ps --status running --services 2>/dev/null | grep -qx db; then
    echo "The server db container is not running. Start it: docker compose -f docker-compose.server.yml up -d" >&2
    exit 1
fi

APP_DB_PASSWORD="$(new_password)"
TEST_DB_PASSWORD="$(new_password)"
# 1. PostgreSQL first. The values reach psql only through the environment of
#    this one docker process (-e NAME without a value copies it from here).
export APP_DB_PASSWORD TEST_DB_PASSWORD
if ! compose exec -T -e APP_DB_PASSWORD -e TEST_DB_PASSWORD db \
        psql -U crm_assistant -d crm_assistant -v ON_ERROR_STOP=1 -q < "$sql_file" >/dev/null; then
    echo "Setting the passwords in PostgreSQL failed - .env was not changed." >&2
    exit 1
fi
# 2. Then .env.
write_env APP_DB_PASSWORD TEST_DB_PASSWORD
unset APP_DB_PASSWORD TEST_DB_PASSWORD
echo "New app and test passwords set in PostgreSQL and $env_file (not displayed)."
# 3. The app reads its password at start; recreate it (and the db, whose
#    environment holds the first-boot values) so both match .env.
compose up -d --force-recreate --no-build db app
echo "Recreated db and app with the new passwords."
