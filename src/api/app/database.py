"""Database access. Schema changes require `alembic upgrade head`; never create on startup."""
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import settings


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def get_engine():
    return make_engine(settings.database_url)


def make_engine(database_url: str):
    if not database_url.startswith("postgresql"):
        raise HTTPException(503, "Account database is not configured.")
    engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)
    if settings.database_iam_auth:
        if not settings.database_ssl_root_cert:
            raise RuntimeError("DATABASE_SSL_ROOT_CERT is required with IAM database authentication.")

        @event.listens_for(engine, "do_connect")
        def connect_with_iam(dialect, connection_record, args, params):
            import boto3
            params["password"] = boto3.client("rds", region_name=settings.cognito_region).generate_db_auth_token(
                DBHostname=engine.url.host, Port=engine.url.port or 5432,
                DBUsername=engine.url.username, Region=settings.cognito_region,
            )
            params["sslmode"] = "verify-full"
            params["sslrootcert"] = settings.database_ssl_root_cert
    return engine


def get_db():
    with Session(get_engine(), expire_on_commit=False) as db:
        yield db
