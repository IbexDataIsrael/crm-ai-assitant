-- Sets the login passwords of crm_app and crm_test. Run only by
-- scripts/set-db-passwords.ps1, which passes the passwords as the environment
-- variables APP_DB_PASSWORD and TEST_DB_PASSWORD of this one psql process.
-- psql's :'name' inserts each value as a correctly quoted SQL literal, so any
-- character is safe; nothing is echoed.

-- Never write these statements to the server log, not even when one fails.
SET log_statement = 'none';
SET log_min_error_statement = 'panic';

\getenv app_password APP_DB_PASSWORD
\getenv test_password TEST_DB_PASSWORD
\if :{?app_password}
\else
  \warn 'APP_DB_PASSWORD was not passed - nothing changed.'
  \quit
\endif
\if :{?test_password}
\else
  \warn 'TEST_DB_PASSWORD was not passed - nothing changed.'
  \quit
\endif

BEGIN;
ALTER ROLE crm_app PASSWORD :'app_password';
ALTER ROLE crm_test PASSWORD :'test_password';
COMMIT;
\unset app_password
\unset test_password
SELECT rolname AS role, rolpassword LIKE 'SCRAM-SHA-256$%' AS scram_password_set
FROM pg_authid WHERE rolname IN ('crm_app', 'crm_test') ORDER BY 1;
