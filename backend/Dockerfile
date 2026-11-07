FROM python:3.12-slim

WORKDIR /app

# Pinned lock first, in its own layer, so an app-code edit never busts the dependency
# cache. requirements.txt exact-pins every dependency pyproject.toml declares as a range
# — see "Dependency pinning" in ../README.md for why and how to refresh it.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

# --no-deps: this only registers the package's own metadata (app/ isn't copied in yet,
# so setuptools finds zero packages) — it must never re-resolve dependencies from
# pyproject.toml's floating ranges and undo the pin installed above.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --no-deps .
COPY app ./app
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# No --reload here: this image is what CI/CD ships as-is to Container Apps
# for dev/qa/prod (see .github/workflows/deploy.yml), and --reload forces a
# single worker process plus a file-watcher — the wrong shape for serving
# concurrent real users. Local docker-compose overrides `command:` to add
# --reload back for hot-reload dev (see docker-compose.yml).
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
