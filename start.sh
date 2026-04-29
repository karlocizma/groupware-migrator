#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"

usage() {
  cat <<'EOF'
Usage:
  ./start.sh [--test] [--no-reload]

Behavior:
  1) Creates .venv if missing
  2) Activates .venv
  3) Installs project editable (pip install -e <project_root>)
  4) Starts API/UI with uvicorn (or runs tests with --test)

Environment variables:
  HOST   (default: 127.0.0.1)
  PORT   (default: 8000)
EOF
}

RUN_TESTS=false
RELOAD_FLAG="--reload"

for arg in "$@"; do
  case "$arg" in
    --test)
      RUN_TESTS=true
      ;;
    --no-reload)
      RELOAD_FLAG=""
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n\n' "$arg" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -d "${VENV_DIR}" ]]; then
  printf 'Creating virtual environment at %s\n' "${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

printf 'Installing project in editable mode...\n'
python3 -m pip install -e "${PROJECT_ROOT}"

if [[ "${RUN_TESTS}" == "true" ]]; then
  printf 'Running unit tests...\n'
  PYTHONPATH="${PROJECT_ROOT}/src" python3 -m unittest discover -s "${PROJECT_ROOT}/tests" -v
  exit 0
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
printf 'Starting Groupware Migrator at http://%s:%s\n' "${HOST}" "${PORT}"

if [[ -n "${RELOAD_FLAG}" ]]; then
  exec uvicorn groupware_migrator.api.app:create_app --factory --host "${HOST}" --port "${PORT}" "${RELOAD_FLAG}"
fi

exec uvicorn groupware_migrator.api.app:create_app --factory --host "${HOST}" --port "${PORT}"
