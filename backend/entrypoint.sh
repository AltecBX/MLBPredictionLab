#!/bin/sh
# Start the API. Migrations are attempted, never a precondition.
#
# This used to be `alembic upgrade head && uvicorn ...`, and that `&&` was a
# single point of failure for the whole product: any migration failure meant
# uvicorn never started, the container exited, and Render served 502 to every
# request until somebody redeployed by hand. The most common cause is not a bad
# migration at all — on a free plan the database wakes more slowly than the web
# service, so the very first `alembic` of a cold deploy can lose a race with a
# database that is fine thirty seconds later.
#
# So: retry migrations a few times with backoff, and start the server either
# way. A read-only API with an unreachable database can still answer, and what
# it answers is an explicit unavailable state that the web app already renders
# properly — naming the source that is missing. That is enormously better than
# not existing, which is what a 502 means to a reader.
#
# The failure is loud in the logs and visible on the diagnostics screen. It is
# not allowed to be silent; it is only stopped from being fatal.
set -u

ATTEMPTS="${MIGRATION_ATTEMPTS:-5}"
DELAY=2
n=1

while [ "$n" -le "$ATTEMPTS" ]; do
  if alembic upgrade head; then
    echo "startup: migrations applied on attempt $n"
    break
  fi
  if [ "$n" -eq "$ATTEMPTS" ]; then
    echo "startup: WARNING migrations failed after $ATTEMPTS attempts." >&2
    echo "startup: WARNING starting the API anyway; endpoints that need the" >&2
    echo "startup: WARNING database will report UNAVAILABLE until it recovers." >&2
    break
  fi
  echo "startup: migration attempt $n failed, retrying in ${DELAY}s" >&2
  sleep "$DELAY"
  DELAY=$((DELAY * 2))
  n=$((n + 1))
done

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
