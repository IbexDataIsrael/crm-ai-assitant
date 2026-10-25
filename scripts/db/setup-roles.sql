-- Admin setup for the CRM Assistant database: roles, grants and schema.
-- Safe to run again (every step is "if missing" or a plain GRANT/REVOKE).
-- No passwords here: scripts/set-db-passwords.ps1 sets them from .env.
--
-- Run as the admin role through the db container's local socket (the only
-- way the admin can log in):
--   Get-Content scripts\db\setup-roles.sql | docker compose exec -T db psql -U crm_assistant -d crm_assistant -v ON_ERROR_STOP=1
--
-- Roles:
--   crm_assistant  superuser from the image's POSTGRES_USER. Admin only: local
--                  socket inside the db container, never over the network.
--   crm_app        the app. Logs in to crm_assistant only; reads and writes
--                  rows of `chunks` (startup sync inserts/updates/deletes rows).
--                  No DDL, no other database.
--   crm_test       the test suite. Same rights, on crm_assistant_test only.

SELECT 'CREATE ROLE crm_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_app') \gexec
SELECT 'CREATE ROLE crm_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_test') \gexec
SELECT 'CREATE DATABASE crm_assistant_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'crm_assistant_test') \gexec

-- By default every role may connect to every database (PUBLIC has CONNECT and
-- TEMP). Take that away so each role reaches only its own database, even if a
-- pg_hba.conf line were ever too broad.
REVOKE ALL ON DATABASE crm_assistant FROM PUBLIC;
REVOKE ALL ON DATABASE crm_assistant_test FROM PUBLIC;
REVOKE ALL ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE crm_assistant TO crm_app;
GRANT CONNECT ON DATABASE crm_assistant_test TO crm_test;

-- Same table as SCHEMA in app/vectorstore.py (keep the two in sync). The app
-- only creates it where it is missing, so with these roles it never runs DDL.
\connect crm_assistant
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    collection   text    NOT NULL,
    filename     text    NOT NULL,
    position     integer NOT NULL,
    title        text    NOT NULL,
    heading      text    NOT NULL,
    text         text    NOT NULL,
    embed_text   text    NOT NULL,
    content_hash text    NOT NULL,
    embedding    vector(384) NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection, filename, position)
);
GRANT USAGE ON SCHEMA public TO crm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON chunks TO crm_app;

\connect crm_assistant_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS chunks (
    collection   text    NOT NULL,
    filename     text    NOT NULL,
    position     integer NOT NULL,
    title        text    NOT NULL,
    heading      text    NOT NULL,
    text         text    NOT NULL,
    embed_text   text    NOT NULL,
    content_hash text    NOT NULL,
    embedding    vector(384) NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection, filename, position)
);
GRANT USAGE ON SCHEMA public TO crm_test;
GRANT SELECT, INSERT, UPDATE, DELETE ON chunks TO crm_test;
