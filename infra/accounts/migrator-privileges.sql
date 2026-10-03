-- Run as the IAM-authenticated reading_sound_migrator, in reading_sound_game,
-- before the first Alembic migration. Safe to rerun as that same role.
-- The RDS master is not a PostgreSQL superuser and should not run this script.
BEGIN;
DO $bootstrap$
BEGIN
  IF current_user <> 'reading_sound_migrator'
     OR current_database() <> 'reading_sound_game' THEN
    RAISE EXCEPTION 'Connect as reading_sound_migrator to reading_sound_game';
  END IF;
END;
$bootstrap$;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO reading_sound_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO reading_sound_app;
COMMIT;

-- Then apply Alembic migrations as this same IAM database user.
-- For a previously migrated database, review table ownership before granting:
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO reading_sound_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO reading_sound_app;
