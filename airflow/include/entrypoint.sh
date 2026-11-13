#!/usr/bin/env bash
# Check the environment, then hand over to the official Airflow entrypoint.
set -euo pipefail

# CECL_ENV is required (docker-compose.yaml sets prod, docker-compose.dev.yaml sets dev).
case "${CECL_ENV:-}" in
  dev|prod) ;;
  *)
    echo "CECL_ENV must be dev or prod for the Airflow stack, got '${CECL_ENV:-<not set>}'." >&2
    echo "It is set in the Compose file; there is deliberately no default. Not starting." >&2
    exit 1
    ;;
esac
python -c "from cecl.environment import describe; print('CECL Airflow:', describe())"

exec /entrypoint "$@"
