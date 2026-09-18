#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${BOT_NEWS_APP_DIR:-/opt/bot_news}"
APP_USER="${BOT_NEWS_USER:-botnews}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root (sudo)." >&2
  exit 1
fi

test -d "${APP_DIR}/.git"
test -x "${APP_DIR}/venv/bin/python"

runuser -u "${APP_USER}" -- git -C "${APP_DIR}" fetch origin
runuser -u "${APP_USER}" -- git -C "${APP_DIR}" checkout main
runuser -u "${APP_USER}" -- git -C "${APP_DIR}" pull --ff-only origin main
runuser -u "${APP_USER}" -- "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

systemctl restart bot-news.service
sleep 3
systemctl --no-pager --full status bot-news.service
