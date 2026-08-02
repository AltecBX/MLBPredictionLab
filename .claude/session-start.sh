#!/usr/bin/env bash
# Prepare a fresh session so tests and linters can run immediately.
# Idempotent and quiet on success; never fails the session.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 0

if [ ! -d .venv ]; then
  python3 -m venv .venv >/dev/null 2>&1 || exit 0
fi
.venv/bin/pip install --quiet --upgrade pip >/dev/null 2>&1
.venv/bin/pip install --quiet -e "backend[dev]" >/dev/null 2>&1

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

if [ ! -d frontend/node_modules ]; then
  (cd frontend && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --no-audit --no-fund >/dev/null 2>&1)
fi

# Bring a local Postgres cluster back up if one exists and nothing is listening.
#
# The documented path is `docker compose up`, and where Docker is available this
# whole block is a no-op. Where it is not — an agent container, a sandbox — the
# database is a bare cluster on disk that does not survive a restart, and every
# backtest command then fails on a connection refused several minutes into a run
# that had no way to know. Starting it here is cheap and silent.
#
# Guarded three ways: only when a data directory actually exists, only when the
# port is free, and only when pg_ctl is installed. Any of those missing and this
# does nothing at all.
db_status=""
if ! pg_isready -q -h 127.0.0.1 -p 5432 2>/dev/null; then
  # Candidates in order of specificity. The hook may run as a different user
  # than the one that created the cluster — root with HOME=/root while the data
  # sits under the project owner's home — so $HOME alone is not enough to find
  # it. The system cluster under /var/lib is deliberately not a candidate:
  # starting an empty one would bind the port and shadow the real database.
  pgdata=""
  for candidate in "${PGDATA:-}" "$HOME/.pgdata" "$(dirname "$PWD")/.pgdata" \
                   "$PWD/.pgdata"; do
    if [ -n "$candidate" ] && [ -f "$candidate/PG_VERSION" ]; then
      pgdata="$candidate"
      break
    fi
  done
  pgctl="$(command -v pg_ctl || ls /usr/lib/postgresql/*/bin/pg_ctl 2>/dev/null | tail -1)"
  if [ -n "$pgdata" ] && [ -n "$pgctl" ]; then
    # A cluster killed rather than stopped leaves a pid file that blocks the
    # restart. Only remove it once the process it names is confirmed gone.
    pidfile="$pgdata/postmaster.pid"
    if [ -f "$pidfile" ] && ! kill -0 "$(head -1 "$pidfile" 2>/dev/null)" 2>/dev/null; then
      rm -f "$pidfile"
    fi
    owner="$(stat -c '%U' "$pgdata" 2>/dev/null)"
    if [ -n "$owner" ] && [ "$owner" != "$(id -un)" ] && [ "$(id -u)" = "0" ]; then
      su "$owner" -c "$pgctl -D '$pgdata' -l '$pgdata/server.log' -w start" >/dev/null 2>&1
    else
      "$pgctl" -D "$pgdata" -l "$pgdata/server.log" -w start >/dev/null 2>&1
    fi
    pg_isready -q -h 127.0.0.1 -p 5432 2>/dev/null && db_status=" + postgres"
  fi
else
  db_status=" + postgres"
fi

echo "Environment ready: .venv + frontend/node_modules${db_status}. Run 'make help' for tasks."
exit 0
