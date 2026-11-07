SHELL := pwsh.exe -NoProfile -Command

.PHONY: setup up down logs test migrate worker-test lock lock-backend lock-worker

setup:
	Copy-Item -Force config/platform.env.example config/platform.env

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

test:
	docker compose run --rm api pytest

worker-test:
	docker compose run --rm worker pytest

# The api container's entrypoint already runs this on every start (§23.12 gap
# #18); use this target to apply pending migrations to a stack that is
# already up, without a restart.
migrate:
	docker compose run --rm api alembic upgrade head

# Regenerate the pinned lock files (requirements.txt / requirements-dev.txt) from
# requirements.in / requirements-dev.in using pip-tools, in a throwaway python:3.12-slim
# container so nobody needs Python installed locally to run it. Use after changing a
# range in pyproject.toml or requirements*.in; CI's *-requirements-lock-check jobs then
# prove the new pins actually install and the app still imports. See "Dependency
# pinning" in README.md.
lock-backend:
	docker run --rm -v "$${PWD}/backend:/src" -w /src python:3.12-slim bash -c "pip install --no-cache-dir pip-tools && pip-compile --no-header --resolver=backtracking -o requirements.txt requirements.in && pip-compile --no-header --resolver=backtracking -o requirements-dev.txt requirements-dev.in"

lock-worker:
	docker run --rm -v "$${PWD}/worker:/src" -w /src python:3.12-slim bash -c "pip install --no-cache-dir pip-tools && pip-compile --no-header --resolver=backtracking -o requirements.txt requirements.in && pip-compile --no-header --resolver=backtracking -o requirements-dev.txt requirements-dev.in"

lock: lock-backend lock-worker
