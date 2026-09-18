# bot_news Linux deployment

`bot_news` is a long-running Telethon client. Deploy it to a Linux VM as its
own systemd service. It does not need a public HTTP port.

## Runtime layout

- Code: `/opt/bot_news`
- Python venv: `/opt/bot_news/venv`
- Runtime data: `/opt/bot_news/data`
- Secrets/config: `/etc/bot-news.env`
- Service: `bot-news.service`

The runtime database and Telethon session are intentionally not stored in Git.

## First deployment

1. Provision an Ubuntu/Debian VM and connect over SSH.
2. Clone or copy this repository, then run:

```bash
sudo bash deploy/linux/install.sh
```

The first run prepares the `botnews` user, repository, venv and data directory.
It does not start the bot until `/etc/bot-news.env` exists.

3. Create `/etc/bot-news.env` as root. Use real values only on the VM:

```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
BOT_NEWS_DB_FILE=/opt/bot_news/data/history.db
BOT_NEWS_SESSION_NAME=/opt/bot_news/data/news_aggregator

AI_GATEWAY_ENABLED=false
AI_GATEWAY_URL=

GROQ_API_KEY=
GEMINI_API_KEY=
HF_TOKEN=
ENABLE_PERSONAL_ALERTS=false
ALERT_BOT_TOKEN=
```

Protect it:

```bash
sudo chown root:root /etc/bot-news.env
sudo chmod 600 /etc/bot-news.env
```

4. Before cutover, copy the existing local runtime files to the VM:

```text
history.db                -> /opt/bot_news/data/history.db
news_aggregator.session   -> /opt/bot_news/data/news_aggregator.session
```

After copying:

```bash
sudo chown -R botnews:botnews /opt/bot_news/data
```

5. Rerun the installer so it installs/enables the unit.

6. Cutover rule: never run the same Telethon user session locally and in the
cloud at the same time. Stop the local bot first, then start the cloud service:

```bash
sudo systemctl start bot-news.service
sudo systemctl status bot-news.service --no-pager
sudo journalctl -u bot-news.service -n 100 --no-pager
```

Only after Telegram receiving/sending is verified in the cloud should the local
Windows watchdog/autostart be disabled.

## Updates

After `main` is updated in GitHub:

```bash
sudo bash /opt/bot_news/deploy/linux/update.sh
```

The script performs a fast-forward-only pull, refreshes Python requirements,
restarts only `bot-news.service`, and prints service status.

## ai_agents integration

While `ai_agents` is not on the same cloud host, keep
`AI_GATEWAY_ENABLED=false` to avoid per-message gateway timeout delays.

When both services are co-located, point `AI_GATEWAY_URL` to the internal
`ai_agents` endpoint and set `AI_GATEWAY_ENABLED=true`.
