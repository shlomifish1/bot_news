#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${BOT_NEWS_REPO_URL:-https://github.com/shlomifish1/bot_news.git}"
APP_DIR="${BOT_NEWS_APP_DIR:-/opt/bot_news}"
APP_USER="${BOT_NEWS_USER:-botnews}"
ENV_FILE="${BOT_NEWS_ENV_FILE:-/etc/bot-news.env}"
UNIT_FILE="/etc/systemd/system/bot-news.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root (sudo)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip git ca-certificates

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"
fi

if [[ -d "${APP_DIR}/.git" ]]; then
  runuser -u "${APP_USER}" -- git -C "${APP_DIR}" fetch origin
  runuser -u "${APP_USER}" -- git -C "${APP_DIR}" checkout main
  runuser -u "${APP_USER}" -- git -C "${APP_DIR}" pull --ff-only origin main
else
  if [[ -d "${APP_DIR}" ]] && [[ -n "$(find "${APP_DIR}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "${APP_DIR} exists and is not an empty git checkout; refusing to overwrite it." >&2
    exit 1
  fi
  rm -rf "${APP_DIR}"
  install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}"
  runuser -u "${APP_USER}" -- git clone --branch main --single-branch "${REPO_URL}" "${APP_DIR}"
fi

install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}/data"

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  runuser -u "${APP_USER}" -- python3 -m venv "${APP_DIR}/venv"
fi

runuser -u "${APP_USER}" -- "${APP_DIR}/venv/bin/python" -m pip install --upgrade pip
runuser -u "${APP_USER}" -- "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

if [[ -f "${ENV_FILE}" ]]; then
  install -m 0644 "${APP_DIR}/deploy/linux/bot-news.service.example" "${UNIT_FILE}"
  systemctl daemon-reload
  systemctl enable bot-news.service
  echo "systemd unit installed and enabled."
  echo "Copy runtime data, then start with: systemctl start bot-news.service"
else
  echo "Code and Python environment are ready."
  echo "Create ${ENV_FILE}, copy runtime data, then rerun this script."
fi
