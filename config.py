# config.py

# --- פרטי התחברות ---
API_ID = 8737347
API_HASH = 'c4502db0cae00e8dcfff5e6b1841dc31'


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
DB_FILE = 'history.db'
SESSION_NAME = 'news_aggregator'
SIMHASH_THRESHOLD = 3   # אגרסיבי יותר (היה 8) – מונע כפילויות שונות במעט
WINDOW_HOURS = 12

# AI fallback settings. Keep them empty by default so the bot can boot in
# zero-AI mode without crashing when no provider keys are configured.
GROQ_API_KEY = ''
GEMINI_API_KEY = ''
HF_TOKEN = ''
FALLBACK_ORDER = []
MODELS = {}

# Personal alerts bridge to an external Telegram bot is disabled by default.
# Keep this OFF to fully disconnect bot_news from AI_AGENTS bot integrations.
ENABLE_PERSONAL_ALERTS = False
ALERT_BOT_TOKEN = ""
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
