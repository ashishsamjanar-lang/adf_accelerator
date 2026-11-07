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

CMD ["python", "-m", "app.main"]
