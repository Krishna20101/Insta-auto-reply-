#!/usr/bin/env bash
# Codespaces terminal se: setup wizard (pehli baar) aur phir foreground bot.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

if ! .venv/bin/python - <<'PY'
from pathlib import Path
required = {"API_ID", "API_HASH", "SESSION_STRING", "BOT_TOKEN", "ADMIN_IDS"}
values = {}
for line in Path(".env").read_text().splitlines() if Path(".env").exists() else []:
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
raise SystemExit(0 if all(values.get(key) for key in required) else 1)
PY
then
  .venv/bin/python generate_session.py
fi

exec .venv/bin/python bot.py
