-- Run once as the RDS-managed master, connected to reading_sound_game.
-- Do not run the website with the master account. Do not grant rds_iam to it.
-- The separate migrator IAM role is used only by the deployment operator.
BEGIN;
CREATE ROLE reading_sound_migrator LOGIN;
CREATE ROLE reading_sound_app LOGIN;
GRANT rds_iam TO reading_sound_migrator, reading_sound_app;
REVOKE ALL ON DATABASE reading_sound_game FROM PUBLIC;
GRANT CONNECT ON DATABASE reading_sound_game TO reading_sound_migrator, reading_sound_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO reading_sound_migrator;
GRANT USAGE ON SCHEMA public TO reading_sound_app;

-- PostgreSQL 17 can automatically grant new roles back to their creator.
-- Remove those memberships before committing IAM-enabled roles. INHERIT FALSE
-- alone is not treated as sufficient protection against nested IAM membership.
REVOKE reading_sound_migrator, reading_sound_app FROM reading_sound_admin;
DO $bootstrap$
BEGIN
  IF pg_has_role('reading_sound_admin', 'rds_iam', 'MEMBER') THEN
    RAISE EXCEPTION 'Unsafe master IAM membership: bootstrap must roll back';
  END IF;
  IF NOT pg_has_role('reading_sound_migrator', 'rds_iam', 'MEMBER')
     OR NOT pg_has_role('reading_sound_app', 'rds_iam', 'MEMBER') THEN
    RAISE EXCEPTION 'Runtime and migrator IAM memberships were not established';
  END IF;
END;
$bootstrap$;
COMMIT;

-- Next, connect using IAM as reading_sound_migrator and run
-- migrator-privileges.sql BEFORE applying Alembic migrations.
-- Do not grant the migrator role back to the master: that could indirectly
-- grant rds_iam to the master and interfere with its password authentication.
