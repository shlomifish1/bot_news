import os
import requests
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai_agents', '.env')
load_dotenv(dotenv_path)

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
ALERT_ADMIN_ID = 165270683

url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
payload = {
    "chat_id": ALERT_ADMIN_ID,
    "text": "🚨 טסט API של הבוט",
}

print(f"Sending to {ALERT_ADMIN_ID} using token {bot_token[:10]}...")
res = requests.post(url, json=payload)
print("Status:", res.status_code)
print("Response:", res.text)
