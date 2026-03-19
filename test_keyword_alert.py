"""
test_keyword_alert.py
מריץ טסט של send_keyword_alert — שולח הודעה לחשבון 'me' (messages שמורות)
כי ה-session הראשי בשימוש על ידי ה-bot.
"""
import sys
import asyncio

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import config
from telethon import TelegramClient

# Session שונה לטסט - כדי לא להתנגש עם ה-bot שרץ
TEST_SESSION = "test_alert_session"

async def main():
    client = TelegramClient(TEST_SESSION, config.API_ID, config.API_HASH)
    await client.start()

    test_text = "שיגור רקטות לכיוון מרכז הארץ | פוליגון פעיל"
    source_title = "ערוץ טסט"
    source_link = None

    from main import ALERT_KEYWORDS
    triggered = [kw for kw in ALERT_KEYWORDS if kw in test_text]
    kw_list = ' | '.join(f'**{kw}**' for kw in triggered)
    footer = f"**{source_title}**"

    alert_msg = (
        f"🚨 **התראת מילת מפתח** — {kw_list}\n"
        f"{'─' * 30}\n"
        f"{test_text}\n\n"
        f"מקור: {footer}\n\n"
        f"_(הודעת טסט - לבדיקת המערכת)_"
    )

    news_channel = config.TARGETS.get('news')
    print(f"Sending test alert to channel: {news_channel}")
    print(f"Triggered keywords: {triggered}")

    await client.send_message(news_channel, alert_msg, link_preview=False)
    print("✅ הודעת טסט נשלחה לערוץ חדשות מרוכזות!")

    await client.disconnect()

asyncio.run(main())
