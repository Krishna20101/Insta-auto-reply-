#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  EK COMMAND — Codespaces terminal me:
#       bash run.sh
#
#  • .env ki ZARURAT NAHI (creds bot.py me hardcoded hain to bhi chalega)
#  • Codespaces Secrets ho to wo bhi chalega
#  • deps install + bot background me + auto-restart + log
# ══════════════════════════════════════════════════════════════════
set -u
cd "$(dirname "$0")"

echo "▶️  [1/2] dependencies install..."
python3 -m pip install -q --upgrade pip 2>/dev/null
python3 -m pip install -q -r requirements.txt 2>&1 | tail -3
echo "    done"

# agar .env hai to load kar lo (na ho to koi baat nahi)
if [ -f .env ]; then set -a; . ./.env; set +a; fi

echo "▶️  [2/2] bot start..."
mkdir -p data
pkill -f "python3 bot.py" 2>/dev/null || true
sleep 1
: > data/bot.log
nohup setsid bash -c 'while true; do python3 bot.py >> data/bot.log 2>&1; echo "[supervisor] 5s me restart..." >> data/bot.log; sleep 5; done' >/dev/null 2>&1 &
sleep 8

echo "──────── last log ────────"
tail -n 25 data/bot.log 2>/dev/null
echo "──────────────────────────"

if grep -qiE "ready|online|started|admin" data/bot.log 2>/dev/null; then
  echo "✅ BOT ONLINE — Telegram me apne bot ko /start bhejo 🎉"
elif grep -qiE "API_ID|API_HASH|BOT_TOKEN|SESSION_STRING|ApiIdInvalid|AccessToken|AuthKey" data/bot.log 2>/dev/null; then
  cat <<'MSG'
⚠️  Creds ki dikkat lag rahi hai. bot.py ke sabse upar ye 5 line honi chahiye:

    API_ID = 36349735
    API_HASH = "373122633b75d964ca4a168c7b79e683"
    BOT_TOKEN = "123456:AA...."        # BotFather wala
    SESSION_STRING = "BQ...."          # user account (copy ke liye zaroori)
    ADMIN_IDS = [123456789]            # aapki telegram id

(naam bilkul same, value quotes "" me). Save karo (Ctrl+S) → phir: bash run.sh
MSG
else
  echo "ℹ️  log dekho:  tail -f data/bot.log"
fi

echo
echo "Commands:  tail -f data/bot.log   |   pkill -f 'python3 bot.py'   |   bash run.sh"
