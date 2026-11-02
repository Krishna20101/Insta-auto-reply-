#!/usr/bin/env bash
# Codespaces create hote hi ek baar chalta hai: deps install + bot background me
set -e
cd "$(dirname "$0")/.."
echo "▶️  pip install..."
python3 -m pip install -q --upgrade pip
python3 -m pip install -q -r requirements.txt || echo "⚠️ pip me dikkat — phir bhi try karenge"

mkdir -p data
echo "▶️  bot start (background, log: data/bot.log)"
bash .devcontainer/resume.sh
