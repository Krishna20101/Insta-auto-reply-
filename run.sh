#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  EK COMMAND — Codespaces terminal me:
#     bash run.sh
#  (deps install karta hai, .env check karta hai, bot background me chalata hai)
# ══════════════════════════════════════════════════════════════
set -u
cd "$(dirname "$0")"

echo "▶️  [1/3] dependencies..."
python3 -m pip install -q --upgrade pip 2>/dev/null
python3 -m pip install -q -r requirements.txt 2>&1 | tail -3

echo "▶️  [2/3] secrets check..."
# .env ko env me load karo
if [ -f .env ]; then set -a; . ./.env; set +a; fi
missing=""
for k in API_ID API_HASH BOT_TOKEN SESSION_STRING ADMIN_IDS; do
  eval "v=\${$k:-}"
  [ -z "$v" ] && missing="$missing $k"
done
if [ -n "$missing" ]; then
  cat <<MSG

❌ Ye missing hain:$missing

Do me se ek karo:

  (A) Codespaces Secrets → GitHub → Settings → Codespaces → Secrets
      naam bilkul same: API_ID, API_HASH, BOT_TOKEN, SESSION_STRING, ADMIN_IDS
      (secret add karne ke baad naya Codespace banana padta hai)

  (B) Sabse aasan — is terminal me .env banao (paste karke values badlo):

      cat > .env <<'X'
      API_ID=1234567
      API_HASH=0123456789abcdef0123456789abcdef
      BOT_TOKEN=123456:AA....
      SESSION_STRING=BQ....
      ADMIN_IDS=123456789
      X
      bash run.sh

MSG
  exit 1
fi
echo "✅ sab secrets mil gaye"

echo "▶️  [3/3] bot start..."
mkdir -p data
pkill -f "python3 bot.py" 2>/dev/null || true
nohup setsid bash -c 'while true; do python3 bot.py >> data/bot.log 2>&1; echo "[supervisor] 5s me restart" >> data/bot.log; sleep 5; done' >/dev/null 2>&1 &
sleep 6
echo "──────── last log ────────"
tail -n 20 data/bot.log 2>/dev/null
echo "──────────────────────────"
if grep -q "ready" data/bot.log 2>/dev/null; then
  echo "✅ BOT ONLINE — Telegram me apne bot ko /start bhejo 🎉"
else
  echo "⚠️  log dekho:  tail -f data/bot.log"
fi
