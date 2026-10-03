import os
from logging.config import fileConfig

from alembic import context

from app.core.config import settings
from app.database import Base, make_engine
from app.models import accounts  # noqa: F401 registers schema

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url="postgresql://", target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    # Tests may provide an isolated SQLite connection; runtime never accepts SQLite.
    run(config.attributes["connection"])
else:
    engine = make_engine(os.getenv("MIGRATION_DATABASE_URL") or settings.database_url)
    with engine.connect() as connection:
        run(connection)
