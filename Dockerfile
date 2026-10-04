FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app/src/api

COPY src/api/pyproject.toml src/api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY src /app/src

# Pin the official RDS trust bundle by digest. Review an upstream change before
# updating this pin; never bypass server certificate verification.
RUN python -c "import hashlib,pathlib,urllib.request; data=urllib.request.urlopen('https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem',timeout=30).read(); assert hashlib.sha256(data).hexdigest()=='fe45bbebf92ad3e27a583bbb2ddd1553c521ed4d49af5514dc0a40372ea5395c', 'RDS certificate bundle changed; review before updating digest'; pathlib.Path('/app/certs').mkdir(); pathlib.Path('/app/certs/rds-global-bundle.pem').write_bytes(data)"

# Deployment archives may be extracted with a private umask. Source and public
# certificates must still be readable by the unprivileged runtime user.
RUN chmod -R a+rX /app/src /app/certs && useradd --system --uid 10001 --create-home appuser && mkdir -p /app/src/api/.cache && chown -R appuser:appuser /app/src/api/.cache
USER appuser

EXPOSE 8080

CMD [".venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
