-- Two database roles, per BACKEND.md §4: "The app's DB user has only the rights it
-- needs: no superuser, no schema changes. Migrations run as a separate user."
--
-- Canonical location. Compose runs this through infra/postgres-init/01-roles.sh on first
-- boot; production runs the same file from the db-bootstrap job (P17), so there is one
-- definition of the privilege model rather than two that drift.
--
-- Idempotent: the production release flow runs it on every deploy.
--
-- Expects psql variables: :app_role :app_password :migrator_role :migrator_password
--
-- NOTE: the existence checks use \gset and \if rather than a plpgsql DO block. psql does
-- not interpolate :variables inside dollar-quoted strings, so a DO block would receive
-- the literal text `:'app_role'` and fail with a syntax error.

-- The app role. Reads and writes rows; cannot change the schema.
select (not exists (select 1 from pg_roles where rolname = :'app_role'))::text as app_absent \gset
\if :app_absent
create role :"app_role" login password :'app_password';
\else
alter role :"app_role" login password :'app_password';
\endif

-- The migration role. Owns the schema; nothing else uses it.
select (not exists (select 1 from pg_roles where rolname = :'migrator_role'))::text as migrator_absent \gset
\if :migrator_absent
create role :"migrator_role" login password :'migrator_password';
\else
alter role :"migrator_role" login password :'migrator_password';
\endif

-- Revoke the PUBLIC grant first. Without this every role can create objects in
-- `public`, which would make the app role's DDL denial silently untrue.
revoke create on schema public from public;

grant usage on schema public to :"app_role";
grant all on schema public to :"migrator_role";

-- Existing objects, for a re-run against a populated database.
grant select, insert, update, delete on all tables in schema public to :"app_role";
grant usage, select on all sequences in schema public to :"app_role";

-- Future objects: whatever the migrator creates, the app can use but not alter.
alter default privileges for role :"migrator_role" in schema public
  grant select, insert, update, delete on tables to :"app_role";
alter default privileges for role :"migrator_role" in schema public
  grant usage, select on sequences to :"app_role";
