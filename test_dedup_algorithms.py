#!/usr/bin/env python3
"""
test_dedup_algorithms.py
========================
בדיקת אלגוריתמי סינון כפילויות וספאם - ללא AI, ללא עלות, אפס קריאות API.

מה הקובץ הזה עושה:
  1. טוען הודעות אמיתיות מה-DB
  2. בודק כל אלגוריתם בנפרד ומציג מדדים
  3. מדמה את זרימת ההודעות כפי שהיא קורית בפועל
  4. לא נוגע כלל במערכת הפעילה!

הרצה:
  cd bot_news
  python test_dedup_algorithms.py
"""

import sqlite3
import re
import math
import time
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

# =========================================================
# CONFIGURATION
# =========================================================

DB_FILE = Path(__file__).parent / "history.db"
WINDOW_HOURS = 12

# סף קיים ב-main.py (Simhash)
SIMHASH_THRESHOLD = 3

# סף ג'קארד מילות מפתח – אחוז חפיפה מינימלי
KEYWORD_JACCARD_THRESHOLD = 0.28

# סף דמיון קוסינוס
COSINE_THRESHOLD = 0.52

# ספאם – ציון מינימלי לחסימה
SPAM_SCORE_THRESHOLD = 7

# גל מילות מפתח – כמה פעמים מילה חוזרת ברצף → חשוד
BURST_WINDOW = 20    # כמה הודעות אחרונות לבדוק
BURST_THRESHOLD = 4  # מילה שמופיעה פי 4+ → גל

# מינימום תווים לבדיקת כפילות (הודעות קצרות מדי לא בודקים)
MIN_LEN_FOR_DEDUP = 20

# =========================================================
# STOP WORDS
# =========================================================

HEB_STOP = {
    'של', 'את', 'אל', 'עם', 'על', 'כי', 'זה', 'לא', 'הם', 'הוא', 'היא',
    'אחר', 'כל', 'עוד', 'אם', 'כן', 'גם', 'כבר', 'רק', 'אבל', 'אז', 'כך',
    'ש', 'ב', 'מ', 'ל', 'ו', 'ה', 'י', 'כ', 'אני', 'אנחנו', 'אתם', 'הן',
    'להם', 'לה', 'לו', 'לנו', 'שלו', 'שלה', 'שלנו', 'שלהם', 'מאוד', 'יותר',
    'פחות', 'לפני', 'אחרי', 'בין', 'כנגד', 'אצל', 'אשר', 'מה', 'מי', 'שם',
    'כאן', 'עכשיו', 'היום', 'מחר', 'אמש', 'רבים', 'אחד', 'שני', 'ניתן',
    'כלל', 'בין', 'יש', 'אין', 'ממש', 'כמו', 'כבר', 'עוד', 'שוב', 'מדי',
    'היה', 'היתה', 'היו', 'יהיה', 'תהיה', 'יהיו', 'להיות', 'בית', 'ידי',
    'ולא', 'אך', 'עם', 'כן', 'בצורה', 'כפי', 'לפי', 'ביחס', 'עד', 'מאז',
    # זמן — כלליים מדי לזיהוי נושא
    'בוקר', 'הבוקר', 'הערב', 'ערב', 'לילה', 'הלילה', 'צהריים', 'הצהריים',
    'שעה', 'שעות', 'דקות', 'שניות', 'כעת', 'השעה',
    # פלטפורמות / מדיה — מופיעות בכל הודעה
    'טלגרם', 'ווצאפ', 'וואטסאפ', 'טוויטר', 'פייסבוק', 'אינסטגרם',
    'ערוץ', 'הערוץ', 'ערוצים', 'פוסט', 'ציוץ', 'שידור', 'מקור',
    # כותרות שאינן ספציפיות לנושא
    'ידיעות', 'ידיעה', 'כותרת', 'כותרות', 'חדשות', 'בשידור',
    # כמות / מדדים כלליים
    'אחוז', 'אחוזים', 'מיליון', 'מיליארד', 'אלף', 'מאות',
}

ENG_STOP = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'in', 'on', 'at', 'to',
    'for', 'of', 'and', 'or', 'but', 'has', 'have', 'had', 'this', 'that',
    'these', 'those', 'it', 'its', 'be', 'been', 'will', 'would', 'could',
    'should', 'may', 'might', 'from', 'with', 'by', 'as', 'if', 'then',
    'than', 'so', 'not', 'no', 'do', 'did', 'does', 'i', 'we', 'he', 'she',
    'they', 'you', 'my', 'our', 'his', 'her', 'their', 'your', 'can', 'all',
    'said', 'say', 'new', 'after', 'before', 'more', 'also', 'about',
    # URL / tech noise
    'https', 'http', 'www', 'com', 'org', 'net', 'gov', 'edu', 'html',
}

# =========================================================
# SPAM PATTERNS (pattern, weight, name)
# =========================================================
# הוסף כאן דפוסים נוספים בהתאם לסוגי הספאם שרואים בפועל

SPAM_PATTERNS = [
    # --- פרסומות עברית ---
    # "מבצע" = deal/promo (commercial) OR military operation.
    # Trigger only when followed by commercial indicators (%,₪,$,הנחה,קופון)
    # or alone without quotes (military ops are usually "מבצע 'שם'" or "מבצע צבאי")
    (r'\bמבצע\b\s*(?=\d|%|₪|\$|ב-\d|ב\d)',  4, 'deal_heb'),  # מבצע 50% / מבצע ₪ / מבצע ב-30
    (r'\bמבצע\s+מ(?:ד|ג|ט|צ)',               3, 'deal_heb2'), # מבצע מטורף/מדהים/גדול
    (r'\bהנחה\b',                   4, 'discount_heb'),
    (r'קוד\s*קופון',                6, 'coupon'),
    (r'\bקופון\b',                  4, 'coupon2'),
    (r'בלעדי\s*לעוקבים',            6, 'exclusive_followers'),
    (r'לחצו\s*(?:כאן|פה)',           5, 'click_here_heb'),
    (r'לחץ\s*(?:כאן|פה)',            5, 'click_here_heb2'),
    (r'הקליקו',                      5, 'click_heb3'),
    (r'צפו\s*(?:עכשיו|:)',           4, 'watch_now_heb'),
    (r'ספר\s*לחברים',               5, 'share_friends'),
    (r'(?:שיתוף|פוסט|תוכן)\s+ממומן', 6, 'sponsored_heb'),
    (r'\bפרסומת\b',                  7, 'ad_heb'),
    (r'מומן\b',                      5, 'sponsored2'),
    (r'שיווקי\b',                    5, 'marketing_heb'),
    (r'הצטרפ[ו|ו]',                  5, 'join_heb'),
    (r'הירשמ[ו|ו]',                  5, 'register_heb'),
    (r'הרשמ[ו|ו]',                   5, 'signup_heb'),
    (r'עקב[ו|ו]\s*אחרינו',           5, 'follow_us_heb'),
    (r'לינק\s*בתגובה',               5, 'link_in_comment'),
    (r'הצטרפ\S*\s*לקבוצ',           6, 'join_group_heb'),
    (r'הצטרפ\S*\s*לערוץ',           6, 'join_channel_heb'),
    (r'ערוץ\s+חדש',                  4, 'new_channel_heb'),
    (r'לפרטים\s+נוספים\s*:',        4, 'more_details'),
    (r'כל\s+הפרטים\s*:',            4, 'all_details'),
    (r'אל\s+תפספס',                  4, 'dont_miss'),
    (r'זמן\s+מוגבל|הזדמנות\s+אחרונה', 5, 'limited_time'),
    # "מכירה" alone can be news ("sale of company"), so require commercial context
    (r'מכירה\s+(?:מיידית|מהירה|עד\s*\d)|סיילים', 4, 'sale_heb'),
    (r'\b₪\s*\d',                    3, 'shekel_price'),
    (r'\$\s*\d',                     3, 'dollar_price'),
    (r'לרכישה',                      4, 'purchase'),
    (r'בחסות',                       4, 'sponsored3'),
    (r'רווחים?\b',                   4, 'profits'),
    (r'פסיבי|הכנסה\s+פסיבית',       5, 'passive_income_heb'),

    # --- קריפטו / השקעות ---
    (r'\bbitcoin\b|\bbtc\b',         6, 'bitcoin'),
    (r'\bcrypto\b|\bcryptocurrency\b', 6, 'crypto'),
    (r'\beth\b|\bethereum\b',        5, 'ethereum'),
    (r'\busdt\b|\bnft\b|\bweb3\b',   5, 'crypto2'),
    (r'\bpump\b|\bmoon\b|\bhodl\b',  5, 'crypto_slang'),
    (r'\bairdrop\b|\btoken\b',       4, 'crypto3'),
    (r'השקעה\s+(?:בטוח|מובטח)',      6, 'guaranteed_investment'),
    (r'תשואה\s+(?:גבוה|מובטח)',      5, 'high_return'),
    (r'הרוויח\S*\s+\d+',            5, 'earn_amount'),

    # --- קישורי טלגרם חשודים ---
    (r't\.me/\+',                    5, 'tg_invite'),
    (r't\.me/joinchat',              5, 'tg_joinchat'),
    (r'wa\.me/',                     4, 'whatsapp_link'),

    # --- דפוסי CTA אנגלית ---
    (r'\bclick\s+here\b',            4, 'click_here_eng'),
    (r'\bsign\s+up\b|\bjoin\s+now\b', 4, 'signup_eng'),
    (r'\bsubscribe\b',               3, 'subscribe_eng'),
    (r'\bmake\s+money\b|\bearn\s+cash\b', 6, 'make_money_eng'),
    (r'\bpassive\s+income\b',        5, 'passive_income_eng'),
    (r'\bfree\s+(?:offer|trial|gift)\b', 4, 'free_eng'),
    (r'\blimited\s+time\b|\bact\s+now\b', 5, 'limited_eng'),
    (r'\binvestment\b',              3, 'investment_eng'),
]

# =========================================================
# דפוסי ניקוי פרסומי (לסוף הודעה) – להסרה בלי AI
# =========================================================
AD_FOOTER_PATTERNS = [
    # קישורי טלגרם / ערוצים בסוף
    r'(?:^|\n)(?:הצטרפ\S*|עקב\S*|לחצ\S*)\s+ל[^\n]{0,80}',
    r'(?:^|\n)(?:לפרטים|לקישור|לשיתוף)[^\n]{0,60}t\.me[^\n]*',
    r'(?:^|\n)@\w{3,}(?:\s[-–]\s[^\n]{0,50})?',
    r'(?:^|\n)מקור\s*:?\s*@\w+[^\n]*',
    r'(?:^|\n)©[^\n]*',
    r'(?:^|\n)\s*\|\s*@\w+[^\n]*',
    r't\.me/(?:joinchat|\+)\S+',
]

# =========================================================
# ALGORITHM A: KEYWORD EXTRACTION
# =========================================================

# תחיליות עבריות חד-אותיות שמצמידות לתחילת מילה
_HEB_PREFIXES = set('לבמכהוש')


def _strip_heb_prefix(word: str) -> str:
    """
    מנסה להסיר תחילית עברית חד-אותית.
    "לאשדוד" → "אשדוד", "וטראמפ" → "טראמפ", "בירושלים" → "ירושלים".
    דורש תוצאה ≥5 תווים — מונע חיתוך שמות זרים קצרים (ביידן→יידן נשמר).
    """
    if len(word) >= 5 and word[0] in _HEB_PREFIXES:
        stripped = word[1:]
        if stripped not in HEB_STOP and len(stripped) >= 5:
            return stripped
    return word


_URL_RE = re.compile(r'https?://\S+|www\.\S+')


def extract_keywords(text: str) -> set:
    """מחלץ מילות מפתח משמעותיות: מספרים, שמות פרטיים, מילים תוכן.
    URLs מוסרים לפני עיבוד למניעת false-positives על 'https','com' וכד'.
    """
    if not text:
        return set()

    # הסר URLs לפני הכל
    clean = _URL_RE.sub(' ', text)

    result = set()

    # מספרים (תמיד חשובים: נפגעים, תאריכים, מספרי גרסה)
    result.update(re.findall(r'\d+', clean))

    # מילים עבריות (≥3 תווים, לא stop words) — רק הצורה המנוקה
    # (לא מוסיפים גם מקורית וגם מנוקה — מונע ניפוח union שמוריד Jaccard)
    heb = re.findall(r'[\u05d0-\u05ea]{3,}', clean)
    for w in heb:
        if w not in HEB_STOP:
            result.add(_strip_heb_prefix(w))

    # מילים אנגליות (≥3 תווים, לא stop words)
    eng = re.findall(r'[A-Za-z]{3,}', clean)
    result.update(w.lower() for w in eng if w.lower() not in ENG_STOP)

    # ציטוטים (שמות ומשפטים בין מרכאות)
    result.update(re.findall(r'"([^"]{2,30})"', clean))
    result.update(re.findall(r"'([^']{2,30})'", clean))

    return result


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# =========================================================
# ALGORITHM B: COSINE SIMILARITY (Bag-of-Words)
# =========================================================

def text_to_bow(text: str) -> Counter:
    tokens = re.findall(r'[\u05d0-\u05ea]+|[A-Za-z]+|\d+', text.lower())
    return Counter(t for t in tokens if len(t) >= 2)


def cosine(c1: Counter, c2: Counter) -> float:
    if not c1 or not c2:
        return 0.0
    shared = set(c1) & set(c2)
    num = sum(c1[k] * c2[k] for k in shared)
    mag1 = math.sqrt(sum(v * v for v in c1.values()))
    mag2 = math.sqrt(sum(v * v for v in c2.values()))
    return num / (mag1 * mag2) if mag1 and mag2 else 0.0


# =========================================================
# ALGORITHM C: EDIT DISTANCE (להודעות קצרות)
# =========================================================

def levenshtein_ratio(s1: str, s2: str) -> float:
    """מחזיר יחס דמיון 0–1 (1 = זהות, 0 = שונה לחלוטין)."""
    if not s1 or not s2:
        return 0.0
    # קצר ל-200 תווים לביצועים
    s1, s2 = s1[:200], s2[:200]
    if abs(len(s1) - len(s2)) / max(len(s1), len(s2), 1) > 0.5:
        return 0.0  # שונים מדי באורך

    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    dist = prev[-1]
    return 1 - dist / max(len(s1), len(s2))


# =========================================================
# ALGORITHM D: KEYWORD BURST DETECTION
# =========================================================

def keyword_burst_check(text: str, recent_texts: list) -> tuple:
    """
    בודק אם מילות מפתח מהטקסט הנוכחי מופיעות בגל בהודעות האחרונות.

    הגיון: אם event X גרם לאותה הודעה לחזור על עצמה, גם מילות המפתח
    הספציפיות שלו (כמו "טראמפ" + "מכסים") חוזרות יחד. אבל אם רק
    מילה כללית אחת חוזרת (כמו "איראן" בזמן מלחמה), ייתכן שמדובר
    בידיעות שונות על אותה מדינה — לכן דורשים לפחות 2 מילות-מפתח
    שחוזרות יחד מעבר לסף.

    מחזיר (is_burst, [(keyword, count), ...]).
    """
    if len(text) < MIN_LEN_FOR_DEDUP:
        return False, []

    curr_kw = extract_keywords(text)
    if not curr_kw:
        return False, []

    hits = Counter()
    for recent in recent_texts[-BURST_WINDOW:]:
        if not recent:
            continue
        hits.update(curr_kw & extract_keywords(recent))

    bursting = [(kw, cnt) for kw, cnt in hits.most_common(8) if cnt >= BURST_THRESHOLD]

    # דרש לפחות 2 מילות-מפתח שחוזרות יחד.
    # מונע false-positives כשמילה כללית אחת (כמו "איראן" במלחמה)
    # מופיעה בהרבה ידיעות שונות.
    if len(bursting) < 2:
        return False, []

    return True, bursting


# =========================================================
# SPAM SCORE ENGINE
# =========================================================

def compute_spam_score(text: str) -> tuple:
    """
    מחזיר (score, matched_patterns_list).
    score ≥ SPAM_SCORE_THRESHOLD → חסום.
    """
    if not text:
        return 0, []

    score = 0
    matched = []

    # דפוסי תבנית
    for pattern, weight, name in SPAM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            matched.append((name, weight))

    # צפיפות אמוג'י
    emoji_count = len(re.findall(
        r'[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\U0001FA00-\U0001FA6F]',
        text
    ))
    words = max(len(text.split()), 1)
    emoji_ratio = emoji_count / words
    if emoji_ratio > 0.35:
        score += 7
        matched.append((f'emoji_density_{emoji_ratio:.2f}', 7))
    elif emoji_ratio > 0.18:
        score += 3
        matched.append((f'emoji_moderate_{emoji_ratio:.2f}', 3))

    # יותר מדי סימני קריאה
    exc = text.count('!')
    if exc >= 4:
        score += min(exc, 6)
        matched.append((f'exclamation_x{exc}', min(exc, 6)))

    # צפיפות URL
    urls = len(re.findall(r'https?://', text))
    if urls >= 3:
        score += 5
        matched.append(('many_urls', 5))
    elif urls == 2:
        score += 2
        matched.append(('two_urls', 2))

    # ביטוי חוזר בתוך ההודעה עצמה (ספאם לרוב חוזר על עצמו)
    # משתמשים ב-4 מילים (לא 3) כדי להימנע מ-false positives בידיעות צבאיות
    words_list = text.split()
    if len(words_list) >= 12:
        fourgrams = [' '.join(words_list[i:i+4]) for i in range(len(words_list) - 3)]
        repeated_fourgrams = [p for p, c in Counter(fourgrams).items() if c >= 2]
        if repeated_fourgrams:
            score += 4
            matched.append(('repeated_phrase_within_msg', 4))

    return score, matched


# =========================================================
# AD CLEANER – מסיר זנבות פרסומיים בלי AI
# =========================================================

def clean_ad_footer(text: str) -> str:
    """
    מסיר שורות בסוף ההודעה שהן טיפוסית קריאות לפעולה, קישורי ערוץ, וכו'.
    מחזיר את הטקסט הנקי, או את המקורי אם לא שינה כלום.
    """
    if not text:
        return text

    cleaned = text
    for pattern in AD_FOOTER_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)

    # נקה שורות ריקות מיותרות בסוף
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = cleaned.strip()

    # אם הניקוי הוריד יותר מ-60% מהטקסט – חשוד שהסרנו יותר מדי, החזר מקור
    if len(cleaned) < len(text) * 0.4:
        return text

    return cleaned


# =========================================================
# COMBINED ENGINE
# =========================================================

class LocalDedupEngine:
    """
    מנוע ניכוי כפילויות וסינון ספאם מקומי – ללא AI, ללא עלות.
    """

    def __init__(self):
        self.kj_thresh = KEYWORD_JACCARD_THRESHOLD
        self.cos_thresh = COSINE_THRESHOLD
        self.spam_thresh = SPAM_SCORE_THRESHOLD

    def check(self, text: str, recent_texts: list) -> dict:
        """
        בודק הודעה אחת מול רשימת הודעות אחרונות.
        מחזיר dict:
          action:        'BLOCK_SPAM' | 'BLOCK_DUP' | 'PASS'
          cleaned_text:  הטקסט לאחר ניקוי (ייתכן שונה מהמקור)
          details:       מידע על כל בדיקה
        """
        if not text or len(text.strip()) < 5:
            return {'action': 'PASS', 'cleaned_text': text, 'details': {}}

        details = {}

        # ---- שלב 1: ספאם ----
        spam_score, spam_matches = compute_spam_score(text)
        details['spam_score'] = spam_score
        details['spam_patterns'] = spam_matches

        if spam_score >= self.spam_thresh:
            return {
                'action': 'BLOCK_SPAM',
                'cleaned_text': text,
                'details': details,
            }

        # ---- שלב 2: כפילויות ----
        is_dup = False
        dup_reason = None

        if recent_texts and len(text) >= MIN_LEN_FOR_DEDUP:

            # B. Keyword Jaccard — דורש ≥2 מילות מפתח משותפות (מונע false-positive על שם בודד)
            curr_kw = extract_keywords(text)
            if len(curr_kw) >= 3:
                best_jac = 0.0
                for r in recent_texts:
                    if r:
                        other_kw = extract_keywords(r)
                        shared = curr_kw & other_kw
                        if len(shared) >= 2:  # require at least 2 shared keywords
                            union = len(curr_kw) + len(other_kw) - len(shared)
                            jac = len(shared) / union if union else 0.0
                            if jac > best_jac:
                                best_jac = jac
                details['keyword_jaccard'] = best_jac
                if best_jac >= self.kj_thresh:
                    is_dup = True
                    dup_reason = f'keyword_jaccard({best_jac:.2f})'

            if not is_dup:
                # C. Cosine similarity (טוב לטקסטים ארוכים)
                curr_bow = text_to_bow(text)
                best_cos = 0.0
                for r in recent_texts:
                    cs = cosine(curr_bow, text_to_bow(r))
                    if cs > best_cos:
                        best_cos = cs
                details['cosine'] = best_cos
                if best_cos >= self.cos_thresh:
                    is_dup = True
                    dup_reason = f'cosine({best_cos:.2f})'

            if not is_dup and len(text) <= 300:
                # D. Edit distance (רק להודעות קצרות)
                best_ratio = 0.0
                for r in recent_texts:
                    if len(r) <= 350:
                        ratio = levenshtein_ratio(text, r)
                        if ratio > best_ratio:
                            best_ratio = ratio
                details['edit_distance_similarity'] = best_ratio
                if best_ratio >= 0.88:
                    is_dup = True
                    dup_reason = f'edit_distance({best_ratio:.2f})'

            if not is_dup:
                # E. Keyword burst
                burst, bursting_kw = keyword_burst_check(text, recent_texts)
                details['keyword_burst'] = bursting_kw
                if burst:
                    is_dup = True
                    dup_reason = f'keyword_burst({bursting_kw[:2]})'

        details['duplicate_reason'] = dup_reason

        if is_dup:
            return {
                'action': 'BLOCK_DUP',
                'cleaned_text': text,
                'details': details,
            }

        # ---- שלב 3: ניקוי זנב פרסומי ----
        cleaned = clean_ad_footer(text)

        return {
            'action': 'PASS',
            'cleaned_text': cleaned,
            'details': details,
        }


# =========================================================
# DB HELPERS
# =========================================================

def load_messages(limit: int = 600) -> list:
    if not DB_FILE.exists():
        print(f"❌ DB לא נמצא: {DB_FILE}")
        return []
    conn = sqlite3.connect(str(DB_FILE))
    cur = conn.cursor()
    cur.execute("""
        SELECT message_text, timestamp, source_id, hash
        FROM messages
        WHERE message_text IS NOT NULL AND length(trim(message_text)) > 10
        ORDER BY timestamp ASC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [{'text': r[0], 'ts': r[1], 'src': r[2], 'hash': r[3]} for r in rows]


def load_ai_moderation_cache(limit: int = 200) -> list:
    """טוען רשומות מטמון הבינה המלאכותית כדי לבחון את הסינון שלנו מולן."""
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(str(DB_FILE))
    cur = conn.cursor()
    cur.execute("""
        SELECT text_hash, result, timestamp
        FROM ai_moderation_cache
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [{'hash': r[0], 'result': r[1], 'ts': r[2]} for r in rows]


# =========================================================
# TEST 1 – ספאם סינתטי (דיוק ידוע מראש)
# =========================================================

SYNTHETIC_SPAM_TESTS = [
    # (text, should_be_spam)
    ("🔥🔥 מבצע מטורף! הצטרפו עכשיו וקבלו קוד קופון! 50% הנחה! 💰💰", True),
    ("ניתוח מדיני: ישראל ולבנון מתקרבות להסכם הפסקת אש", False),
    ("BITCOIN PUMP! BUY NOW! 🚀🚀🚀 Join our crypto group t.me/+abc123", True),
    ("הצבא האמריקאי הגביר את נוכחותו בים הסוף לאחר תקיפות חות'ים", False),
    ("הצטרפו לקבוצת ה-VIP שלנו ותרוויחו 1000$ ביום ללא מאמץ!", True),
    ("נשיא ארה\"ב הודיע על שיחות שלום מחודשות עם רוסיה", False),
    ("🎁 בלעדי לעוקבים! ספר לחברים וקבל הנחה נוספת!", True),
    ("טיל בליסטי שוגר מתימן לעבר אילת - הוירוט הצליח", False),
    ("פרסומת: השקעה מובטחת עם תשואה של 30% בחודש! אל תפספסו!", True),
    ("ראש הממשלה הודיע הלילה על שינויים מבניים בממשלה", False),
    ("Crypto airdrop! Get free ETH tokens by joining our Telegram channel", True),
    ("IDF strikes multiple targets in Gaza in overnight operation", False),
    ("לינק בתגובה 👇 הצטרפו לערוץ החדש שלנו לעוד תוכן!", True),
    ("שר האוצר הכריז על תוכנית כלכלית חדשה לצמצום הגירעון", False),
    ("Investment opportunity! 💰 Limited time offer. Click here now!", True),
    ("חבורת חמאס ירתה רקטות לעבר שדרות הלילה", False),
    ("קנו עכשיו ב-50% הנחה! זמן מוגבל בלבד ✅ ₪199 במקום ₪399", True),
    ("ועדת החוץ והביטחון אישרה את הסכם הפסקת האש בפה אחד", False),
]


def run_synthetic_spam_test():
    print("\n" + "=" * 68)
    print("  TEST 1 – SPAM DETECTION (SYNTHETIC, KNOWN GROUND TRUTH)")
    print("=" * 68)

    engine = LocalDedupEngine()
    correct = 0
    false_pos = []   # ידיעה לגיטימית שנחסמה בטעות
    false_neg = []   # ספאם שעבר

    print(f"\n{'✓':<3} {'Expected':<10} {'Got':<12} {'Score':<7} Text[:55]")
    print("-" * 80)

    for text, expected_spam in SYNTHETIC_SPAM_TESTS:
        result = engine.check(text, [])
        got_spam = result['action'] == 'BLOCK_SPAM'
        score = result['details'].get('spam_score', 0)
        ok = got_spam == expected_spam
        if ok:
            correct += 1
        else:
            if got_spam and not expected_spam:
                false_pos.append((text, score))
            else:
                false_neg.append((text, score))

        mark = '✅' if ok else '❌'
        exp_str = 'SPAM  ' if expected_spam else 'LEGIT '
        got_str = 'SPAM  ' if got_spam else 'LEGIT '
        print(f"{mark}  {exp_str}     {got_str}     {score:<7} {text[:55]}")

    total = len(SYNTHETIC_SPAM_TESTS)
    acc = correct / total * 100
    print(f"\n{'─'*68}")
    print(f"  Accuracy: {correct}/{total}  ({acc:.0f}%)")
    if false_pos:
        print(f"\n  ⚠️  False Positives (legitimate news blocked):")
        for t, s in false_pos:
            print(f"     score={s}  {t[:70]}")
    if false_neg:
        print(f"\n  ⚠️  False Negatives (spam passed through):")
        for t, s in false_neg:
            print(f"     score={s}  {t[:70]}")
    return acc


# =========================================================
# TEST 2 – גל הודעות כפולות (Trump Scenario)
# =========================================================

def run_burst_simulation():
    print("\n" + "=" * 68)
    print("  TEST 2 – KEYWORD BURST SIMULATION (Trump news wave)")
    print("=" * 68)

    engine = LocalDedupEngine()
    messages = [
        # גל ראשון – כולן על אותה עובדה
        ("טראמפ הכריז על תוכנית כלכלית חדשה", False),
        ("הנשיא טראמפ מתכנן להטיל מכסים על סין בשיעור של 25%", False),
        ("Trump announces new 25% tariffs on Chinese goods", False),
        # פרשנויות על אותה ידיעה
        ("מנתחים: מכסי טראמפ על סין יובילו למלחמת סחר", True),   # כפילות (אותו אירוע)
        ("ביידן מגיב לצעדי טראמפ בנוגע למכסים על סין", True),    # עדיין אותו אירוע
        ("שוק המניות: ירידות בעקבות הכרזת טראמפ על מכסים", True),# אותו אירוע
        # ידיעה שונה לחלוטין
        ("רעידת אדמה בסייכל, איסלנד – 5.8 בסולם ריכטר", False),  # אחרת לגמרי
        # ידיעה חדשה על טראמפ – אחרת
        ("Trump visits Saudi Arabia for energy summit", False),
    ]

    recent = []
    print()
    for msg, expected_dup in messages:
        result = engine.check(msg, recent)
        action = result['action']
        burst = result['details'].get('keyword_burst', [])
        jac = result['details'].get('keyword_jaccard', 0)
        cos = result['details'].get('cosine', 0)
        got_dup = action == 'BLOCK_DUP'
        ok = got_dup == expected_dup
        mark = '✅' if ok else '❌'
        reason = result['details'].get('duplicate_reason') or action
        print(f"  {mark}  {'PASS' if action=='PASS' else 'BLOCK':6}  jac={jac:.2f} cos={cos:.2f}  {msg[:55]}")
        if burst:
            top = burst[:3]
            print(f"         burst_kw: {top}")
        if action == 'PASS':
            recent.append(msg)


# =========================================================
# TEST 3 – הודעות אמיתיות מה-DB
# =========================================================

def run_real_data_test():
    print("\n" + "=" * 68)
    print("  TEST 3 – REAL DATABASE MESSAGES")
    print("=" * 68)

    messages = load_messages(limit=600)
    if not messages:
        print("  ⚠️  לא נמצאו הודעות ב-DB. מדלג.")
        return

    print(f"\n  נטענו {len(messages)} הודעות מה-DB")

    engine = LocalDedupEngine()
    stats = {
        'total': len(messages),
        'spam': 0,
        'dup': 0,
        'passed': 0,
        'by_algo': defaultdict(int),
        'spam_triggers': defaultdict(int),
    }

    recent_window = []
    results = []

    t0 = time.perf_counter()
    for msg in messages:
        text = msg['text']
        result = engine.check(text, recent_window)
        result['msg'] = msg
        results.append(result)

        if result['action'] == 'BLOCK_SPAM':
            stats['spam'] += 1
            for name, _ in result['details'].get('spam_patterns', []):
                stats['spam_triggers'][name] += 1

        elif result['action'] == 'BLOCK_DUP':
            stats['dup'] += 1
            reason = result['details'].get('duplicate_reason', 'unknown')
            algo = re.split(r'[\(\[]', reason)[0].strip() if reason else 'unknown'
            stats['by_algo'][algo] += 1

        else:
            stats['passed'] += 1
            recent_window.append(text)
            if len(recent_window) > 30:
                recent_window.pop(0)

    elapsed = time.perf_counter() - t0
    per_msg_ms = elapsed / max(stats['total'], 1) * 1000

    total = stats['total']
    print(f"\n  ⏱  {elapsed:.3f}s  ({per_msg_ms:.2f}ms/הודעה)")
    print(f"\n  📊 סיכום:")
    print(f"     סה\"כ הודעות:     {total:>5}")
    print(f"     ✅ עברו:          {stats['passed']:>5}  ({stats['passed']/total*100:.1f}%)")
    print(f"     🔴 ספאם/פרסומת:  {stats['spam']:>5}  ({stats['spam']/total*100:.1f}%)")
    print(f"     🟡 כפילויות:      {stats['dup']:>5}  ({stats['dup']/total*100:.1f}%)")

    print(f"\n  📈 כפילויות לפי אלגוריתם:")
    for algo, cnt in sorted(stats['by_algo'].items(), key=lambda x: -x[1]):
        print(f"     {algo:<35} {cnt:>4}")

    print(f"\n  🚫 טריגרי ספאם עיקריים:")
    for trigger, cnt in sorted(stats['spam_triggers'].items(), key=lambda x: -x[1])[:12]:
        print(f"     {trigger:<35} {cnt:>4}")

    # דוגמאות כפילויות שנתפסו
    dup_samples = [r for r in results if r['action'] == 'BLOCK_DUP'][:6]
    if dup_samples:
        print(f"\n  🟡 דוגמאות כפילויות שנתפסו:")
        for r in dup_samples:
            reason = r['details'].get('duplicate_reason', '')
            preview = r['msg']['text'][:80].replace('\n', ' ')
            print(f"     [{reason}]")
            print(f"     {preview}")
            print()

    # דוגמאות ספאם שנתפס
    spam_samples = [r for r in results if r['action'] == 'BLOCK_SPAM'][:4]
    if spam_samples:
        print(f"\n  🔴 דוגמאות ספאם שנתפסו:")
        for r in spam_samples:
            score = r['details'].get('spam_score', 0)
            triggers = [p[0] for p in r['details'].get('spam_patterns', [])][:3]
            preview = r['msg']['text'][:80].replace('\n', ' ')
            print(f"     score={score} triggers={triggers}")
            print(f"     {preview}")
            print()

    # השוואת ביצועים
    print(f"  ⚡ השוואת ביצועים:")
    print(f"     אלגוריתמי:  {per_msg_ms:.2f}ms/הודעה  | עלות: חינם")
    print(f"     Gemini AI:  ~800–2000ms/הודעה | עלות: מכסת API")
    print(f"     מהירות:     פי ~{int(1200/max(per_msg_ms,0.1))}x יותר מהיר")

    return results


# =========================================================
# TEST 4 – ניקוי זנב פרסומי
# =========================================================

def run_ad_cleaner_test():
    print("\n" + "=" * 68)
    print("  TEST 4 – AD FOOTER CLEANER")
    print("=" * 68)

    examples = [
        (
            "ישראל ולבנון חתמו על הסכם גבול ימי.\n\n"
            "📢 הצטרפו לערוץ החדש שלנו לעוד עדכונים!\n"
            "@NewsChannelHeb - הערוץ המוביל לחדשות",
            "ישראל ולבנון חתמו על הסכם גבול ימי.",
        ),
        (
            "ראש הממשלה נפגש עם נשיא ארה\"ב בוושינגטון לדיון בנושא הסכמי שלום.\n\n"
            "© ערוץ 12 חדשות 2024",
            "ראש הממשלה נפגש עם נשיא ארה\"ב בוושינגטון לדיון בנושא הסכמי שלום.",
        ),
        (
            "IDF troops entered the northern sector in a ground operation.\n"
            "For more updates: t.me/+xyz123invite",
            "IDF troops entered the northern sector in a ground operation.",
        ),
    ]

    print()
    for original, expected_clean in examples:
        cleaned = clean_ad_footer(original)
        ok = expected_clean.strip() in cleaned or cleaned.strip() == expected_clean.strip()
        mark = '✅' if ok else '⚠️'
        print(f"  {mark} Original : {original[:70].replace(chr(10),' ')}")
        print(f"     Cleaned  : {cleaned[:70].replace(chr(10),' ')}")
        print()


# =========================================================
# TEST 5 – מה יקרה כשמשלבים הכל ב-main_handler
# =========================================================

def run_integration_simulation():
    print("\n" + "=" * 68)
    print("  TEST 5 – INTEGRATION FLOW SIMULATION")
    print("=" * 68)
    print("  מדמה את main_handler עם המנוע החדש (ללא AI)")

    # מניח שרשימת מילים חסומות נשמרת כמות שהיא
    BAD_WORDS = [
        "מבצע", "הנחה", "לרכישה", "קוד קופון", "בחסות",
        "crypto", "bitcoin", "investment", "הצטרפו לערוץ",
        "בלעדי לעוקבים", "שיווקי", "מומן", "₪", "% הנחה",
        "כל הפרטים", "לפרטים נוספים", "הקליקו", "הירשמו עכשיו",
        "עקבו אחרינו", "צפו:", "לינק בתגובה", "הצטרפו לקבוצה",
        "קופון", "הנחות", "מכירה", "סיילים", "רווחים", "דולרים",
        "הזדמנות אחרונה", "זמן מוגבל", "אל תפספסו", "פרסומת"
    ]

    engine = LocalDedupEngine()
    stream = [
        "פיגוע יירוט: כיפת ברזל יירטה רקטה מעל אשדוד",
        "כיפת ברזל יירטה רקטה שנורתה מרצועת עזה לאשדוד",  # כפילות
        "IDF: Iron Dome intercepted a rocket over Ashdod",     # כפילות
        "ביידן: 'ארה\"ב מחויבת לביטחון ישראל'",
        "🔥 מבצע גדול! הנחה של 70% על כל המוצרים! הצטרפו לקבוצת הVIP! 💎",  # BAD_WORDS
        "ממשלת ישראל אישרה תקציב ביטחון חדש לשנת 2026",
        "ממשלת ישראל מאשרת תקציב ביטחוני מוגדל לשנה הבאה", # כפילות
        "Crypto pump! Buy BTC now! 🚀 t.me/+abc investment opportunity", # SPAM
        "שב\"כ מפרסם: סוכל פיגוע בירושלים בשיתוף פעולה עם הרשות הפלסטינית",
    ]

    recent = []
    print()
    for text in stream:
        # שכבה 0: BAD_WORDS (נשארת כמות שהיא)
        blocked_bw = any(w.lower() in text.lower() for w in BAD_WORDS)
        if blocked_bw:
            print(f"  🗑  [BAD_WORDS]  {text[:65]}")
            continue

        result = engine.check(text, recent)
        action = result['action']

        if action == 'BLOCK_SPAM':
            score = result['details'].get('spam_score', 0)
            print(f"  🔴 [SPAM s={score}]  {text[:65]}")
        elif action == 'BLOCK_DUP':
            reason = result['details'].get('duplicate_reason', '')
            print(f"  🟡 [DUP {reason[:30]}]  {text[:55]}")
        else:
            cleaned = result['cleaned_text']
            changed = cleaned != text
            tag = ' (cleaned)' if changed else ''
            print(f"  ✅ [PASS{tag}]  {text[:65]}")
            recent.append(cleaned)
            if len(recent) > 30:
                recent.pop(0)


# =========================================================
# MAIN
# =========================================================

def main():
    print("\n" + "#" * 68)
    print("  BOT_NEWS - ALGORITHMIC DEDUP ENGINE TEST")
    print("  Zero AI calls | Zero cost | Real data test")
    print("#" * 68)

    # Test 1: ספאם סינתטי
    acc = run_synthetic_spam_test()

    # Test 2: גל הודעות
    run_burst_simulation()

    # Test 3: נתונים אמיתיים מה-DB
    run_real_data_test()

    # Test 4: ניקוי זנבות
    run_ad_cleaner_test()

    # Test 5: סימולציית אינטגרציה
    run_integration_simulation()

    print("\n" + "=" * 68)
    print(f"  SPAM TEST ACCURACY: {acc:.0f}%")
    print("  כל הבדיקות הסתיימו. לא בוצעה שום קריאת AI.")
    print("  אם התוצאות מספקות – עדכן את main.py!")
    print("=" * 68 + "\n")


if __name__ == '__main__':
    main()
