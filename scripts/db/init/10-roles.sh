#!/bin/sh
# First boot of an EMPTY database volume only (the postgres image runs
# /docker-entrypoint-initdb.d once, while the data directory is new). Mounted
# by docker-compose.server.yml; the dev profile does not use it.
#
# Creates the roles, databases, table and grants (setup-roles.sql) and sets
# the crm_app / crm_test passwords from this container's APP_DB_PASSWORD and
# TEST_DB_PASSWORD (set-passwords.sql reads them with \getenv; nothing is
# printed or logged). Runs as the admin over the local socket (peer).
# Any failure stops the first boot, so the volume is never left half set up.
set -e

if [ -z "$APP_DB_PASSWORD" ] || [ -z "$TEST_DB_PASSWORD" ]; then
    echo "APP_DB_PASSWORD / TEST_DB_PASSWORD missing - run scripts/set-db-passwords.sh first." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 --no-psqlrc -q --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -f /etc/postgresql/sql/setup-roles.sql
psql -v ON_ERROR_STOP=1 --no-psqlrc -q --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -f /etc/postgresql/sql/set-passwords.sql
