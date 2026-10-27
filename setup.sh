#!/usr/bin/env bash
# One-time setup inside WSL Ubuntu (or Linux/macOS). Run from the project folder: bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "Install Python 3: sudo apt install -y python3 python3-venv"; exit 1; }
python3 -m venv .venv 2>/dev/null || { sudo apt update && sudo apt install -y python3-venv && python3 -m venv .venv; }
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Created .env from .env.example. Fill it in."; }

docker compose up -d evidence-db
echo "Waiting for the evidence store..."
for i in $(seq 1 30); do
  docker compose exec -T evidence-db pg_isready -U seismo -d seismograph >/dev/null 2>&1 && break
  sleep 1
done
python -m seismograph init-db
python -m pytest -q
echo
echo "Done. Next: register the evidence store in Zetaris (sql/02_zetaris_register.sql), fill in .env,"
echo "then: . .venv/bin/activate && python -m seismograph check"
