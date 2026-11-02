#!/usr/bin/env bash
# Bot ko background me chalata hai (dobara chalane par duplicate nahi banega)
cd "$(dirname "$0")/.."
mkdir -p data
if pgrep -f "python3? bot.py" >/dev/null 2>&1; then
  echo "✅ bot already chal raha hai"
  exit 0
fi
if [ -z "$BOT_TOKEN" ]; then
  cat <<'MSG'
⚠️  BOT_TOKEN nahi mila.
   GitHub → Settings → Codespaces → Secrets me ye add karo:
     API_ID, API_HASH, BOT_TOKEN, SESSION_STRING, ADMIN_IDS
   Phir is terminal me:  bash .devcontainer/resume.sh
MSG
  exit 0
fi
nohup setsid bash -c 'while true; do python3 bot.py >> data/bot.log 2>&1; echo "[supervisor] restart in 5s" >> data/bot.log; sleep 5; done' >/dev/null 2>&1 &
sleep 3
tail -n 15 data/bot.log 2>/dev/null || true
echo "✅ bot background me chal raha hai — Telegram me /start dabao"
