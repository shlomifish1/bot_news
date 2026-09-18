# config.py

import os

from dotenv import load_dotenv

load_dotenv()


def _required_int_env(name: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        raise ValueError(f"Missing required environment variable: {name}")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer") from exc


def _required_text_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


# --- פרטי התחברות ---
API_ID = _required_int_env("TELEGRAM_API_ID")
API_HASH = _required_text_env("TELEGRAM_API_HASH")


# הגדרת המנהל: מי מקבל את השאלות מהבוט?
# 'me' = הודעות שמורות בחשבון האמריקאי עצמו
ADMIN_USER = 'me'

# --- ערוצי היעד שלך ---
TARGETS = {
    'news': -1003743047624,   # חדשות מרוכזות
    'tech': -1003509246581,   # טכנולוגיה
    'sport': -1003892013173   # ספורט
}


# --- הגדרות מערכת ---
DB_FILE = os.getenv("BOT_NEWS_DB_FILE", "history.db").strip() or "history.db"
SESSION_NAME = os.getenv("BOT_NEWS_SESSION_NAME", "news_aggregator").strip() or "news_aggregator"
SIMHASH_THRESHOLD = 3   # אגרסיבי יותר (היה 8) – מונע כפילויות שונות במעט
WINDOW_HOURS = 12

# --- Sports web/RSS ingestion ---
# Polls public sports sources and sends only articles that match the team watchlist.
ENABLE_SPORT_WEB_SOURCES = True
SPORT_WEB_POLL_INTERVAL_SEC = 20 * 60
SPORT_WEB_MAX_ITEMS_PER_SOURCE = 3
SPORT_WEB_OTHER_TEAM_DAILY_LIMIT = 3
SPORT_WEB_GENERAL_DAILY_LIMIT = 0

SPORT_WEB_RSS_SOURCES = [
    {
        "name": "Walla Sports",
        "url": "https://rss.walla.co.il/feed/3?type=main",
    },
    {
        "name": "ONE",
        "url": "https://www.one.co.il/rss/",
    },
    {
        "name": "Ynet Sport",
        "url": "https://www.ynet.co.il/Integration/StoryRss3.xml",
    },
]

SPORT_WEB_365SCORES_SOURCES = [
    {
        "name": "365Scores - Hapoel Petah Tikva",
        "url": "https://www.365scores.com/he/football/team/hapoel-petah-tikva-571/news",
        "competitor_id": 571,
        "source_tag": "hapoel_pt_football",
    },
    {
        "name": "365Scores - Real Madrid",
        "url": "https://www.365scores.com/he/football/team/real-madrid-131/news",
        "competitor_id": 131,
        "source_tag": "real_madrid_football",
    },
    {
        "name": "365Scores - Maccabi Tel Aviv Basketball",
        "url": "https://www.365scores.com/he/basketball/team/maccabi-tel-aviv-631/news",
        "competitor_id": 631,
        "source_tag": "maccabi_ta_basketball",
    },
]

SPORT_WEB_HTML_SOURCES = [
    {
        "name": "Maccabi Tel Aviv Basketball Official",
        "url": "https://maccabi.co.il/Archive.asp?cType=0",
        "source_tag": "maccabi_ta_basketball",
    },
    {
        "name": "Hapoel Petah Tikva Official",
        "url": "https://www.hapoelpt.com/news",
        "source_tag": "hapoel_pt_football",
    },
]

SPORT_FAVORITE_TEAMS = {
    "hapoel_pt_football": {
        "display": "הפועל פתח תקווה רגל",
        "keywords": [
            "הפועל פתח תקוה", "הפועל פתח תקווה", "הפועל פ\"ת",
            "הפועל פתח-תקוה", "הפועל פתח-תקווה", "hapoel petah tikva",
        ],
    },
    "maccabi_ta_basketball": {
        "display": "מכבי תל אביב סל",
        "keywords": [
            "מכבי תל אביב", "מכבי ת\"א", "מכבי תא", "maccabi tel aviv",
            "maccabi rapyd", "maccabi playtika",
        ],
        "required_context": [
            "כדורסל", "יורוליג", "euroleague", "basketball", "סל",
            "ליגת העל בכדורסל", "היכל", "קטש", "סורקין",
        ],
    },
    "real_madrid_football": {
        "display": "ריאל מדריד רגל",
        "keywords": ["ריאל מדריד", "real madrid", "real-madrid"],
        "blocked_context": ["כדורסל", "basketball", "baloncesto"],
    },
}

SPORT_OTHER_TEAM_KEYWORDS = {
    "maccabi_ta_football": ["מכבי תל אביב", "מכבי ת\"א", "maccabi tel aviv"],
    "maccabi_haifa": ["מכבי חיפה"],
    "hapoel_ta": ["הפועל תל אביב", "הפועל ת\"א"],
    "beitar_jerusalem": ["בית\"ר ירושלים", "ביתר ירושלים"],
    "hapoel_beer_sheva": ["הפועל באר שבע", "הפועל ב\"ש"],
    "barcelona": ["ברצלונה", "barcelona"],
    "atletico_madrid": ["אתלטיקו מדריד", "atletico madrid"],
    "man_city": ["מנצ'סטר סיטי", "manchester city", "man city"],
    "man_united": ["מנצ'סטר יונייטד", "manchester united", "man united"],
    "liverpool": ["ליברפול", "liverpool"],
    "arsenal": ["ארסנל", "arsenal"],
    "chelsea": ["צ'לסי", "chelsea"],
    "psg": ["פ.ס.ז", "פסז", "psg"],
    "bayern": ["באיירן", "bayern"],
    "juventus": ["יובנטוס", "juventus"],
    "inter": ["אינטר", "inter"],
    "milan": ["מילאן", "ac milan"],
}

# AI fallback settings. Keep them empty by default so the bot can boot in
# zero-AI mode without crashing when no provider keys are configured.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
FALLBACK_ORDER = []
MODELS = {}

# Personal alerts bridge to an external Telegram bot is disabled by default.
# Keep this OFF to fully disconnect bot_news from AI_AGENTS bot integrations.
ENABLE_PERSONAL_ALERTS = os.getenv("ENABLE_PERSONAL_ALERTS", "false").strip().lower() in {
    "1", "true", "yes", "on",
}
ALERT_BOT_TOKEN = os.getenv("ALERT_BOT_TOKEN", "").strip()
ALERT_ADMIN_ID = 165270683

# --- רשימת מילים חסומות (Anti-Spam) ---
BAD_WORDS = [
    "מבצע", "הנחה", "לרכישה", "קוד קופון", "בחסות", 
    "crypto", "bitcoin", "investment", "הצטרפו לערוץ", 
    "בלעדי לעוקבים", "שיווקי", "מומן", "₪", "% הנחה",
    "כל הפרטים", "לפרטים נוספים", "הקליקו", "הירשמו עכשיו",
    "עקבו אחרינו", "צפו:", "לינק בתגובה", "הצטרפו לקבוצה",
    "קופון", "הנחות", "מכירה", "סיילים", "רווחים", "דולרים",
    "הזדמנות אחרונה", "זמן מוגבל", "אל תפספסו", "פרסומת"
]
