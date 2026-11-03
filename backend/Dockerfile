FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" PGWEB_DATA_DIR=/data

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN useradd -r -u 1000 app && mkdir /data && chown app /data
USER app
EXPOSE 8000
# One worker on purpose: manual transactions are held in this process's memory.
# Compose passes the exact Nginx peer address; never trust forwarded headers from every peer.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=\"${PGWEB_WEB_IP:-172.30.0.20}\""]
