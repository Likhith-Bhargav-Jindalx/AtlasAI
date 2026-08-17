FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker-entrypoint.sh ./

RUN pip install --no-cache-dir . && chmod +x docker-entrypoint.sh

ENV ATLAS_RUN_ROOT=/data/atlas-runs
RUN mkdir -p /data/atlas-runs

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
