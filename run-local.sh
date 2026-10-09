#!/usr/bin/env bash
# Menjalankan Auto Post di komputer sendiri (Mac/Linux): ./run-local.sh
# Port bisa diganti: PORT=8080 ./run-local.sh
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

PY=""
for cmd in python3 python; do
  if command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$cmd"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 atau lebih baru belum terpasang. Unduh di https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Membuat virtual environment (.venv)..."
  "$PY" -m venv .venv
fi
echo "Memasang/memeriksa dependency..."
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

.venv/bin/python scripts/setup_env.py "$PORT"
exec .venv/bin/python -m uvicorn app.main:app --port "$PORT"
