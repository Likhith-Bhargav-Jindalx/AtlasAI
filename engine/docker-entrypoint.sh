#!/usr/bin/env sh
set -e
alembic upgrade head
exec uvicorn atlas_engine.api:app --host 0.0.0.0 --port 8000
