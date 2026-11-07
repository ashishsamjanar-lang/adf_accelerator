#!/bin/sh
# Run migrations before serving traffic (AGENTS.md §23.12 gap #18 — previously
# `alembic upgrade head` was never run anywhere; the API relied on
# `Base.metadata.create_all` at startup instead, so the checked-in migration
# was unverified). `docker compose run --rm api pytest` also goes through this
# entrypoint, which is fine: postgres is a healthy `depends_on` by then.
set -e

attempt=1
until alembic upgrade head; do
    if [ "$attempt" -ge 5 ]; then
        echo "alembic upgrade head failed after $attempt attempts" >&2
        exit 1
    fi
    echo "alembic upgrade head failed (attempt $attempt) — retrying in 2s..." >&2
    attempt=$((attempt + 1))
    sleep 2
done

exec "$@"
