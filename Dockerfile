FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY github_metrics.py collector.py ./
COPY dashboard/ dashboard/

RUN mkdir -p /app/data

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000

ENV DATA_DIR=/app/data \
    FLASK_DEBUG=0 \
    COLLECT_MONTHS=12 \
    CRON_SCHEDULE="0 6 * * 1"

ENTRYPOINT ["/entrypoint.sh"]
