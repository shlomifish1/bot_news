"""
dedup_engine.py
===============
מנוע ניכוי כפילויות וסינון ספאם — אלגוריתמי לחלוטין.
אפס קריאות AI | אפס עלות | פי ~50 מהיר מ-Gemini/Groq.

שימוש:
    from dedup_engine import LocalDedupEngine
    engine = LocalDedupEngine()

    result = engine.check(text, recent_texts)
    # result['action']       →  'BLOCK_SPAM' | 'BLOCK_DUP' | 'PASS'
    # result['cleaned_text'] →  טקסט לאחר הסרת זנבות פרסומיים
    # result['details']      →  פירוט כל בדיקה (לדיבאג)
"""

import re
import math
from collections import Counter

# =========================================================
# CONFIGURATION (ניתן לשנות מה-config.py)
# =========================================================

KEYWORD_JACCARD_THRESHOLD = 0.20  # חפיפת מילות מפתח ≥ 20% → כפילות (היה 0.34 → 0.28 → 0.20)
COSINE_THRESHOLD = 0.42           # דמיון קוסינוס ≥ 0.42 → כפילות (היה 0.52 → 0.42)
SPAM_SCORE_THRESHOLD = 7          # ציון ספאם ≥ 7 → חסום
BURST_WINDOW = 20                 # כמה הודעות אחרונות לבדוק בגל
BURST_THRESHOLD = 4               # מילה חוזרת 4+ פעמים → גל
MIN_LEN_FOR_DEDUP = 20            # מינימום תווים לבדיקת כפילות (היה 40 → 20 לתפוס פלאשים קצרים)

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
    'כלל', 'יש', 'אין', 'ממש', 'כמו', 'שוב', 'מדי', 'היה', 'היתה', 'היו',
    'יהיה', 'תהיה', 'יהיו', 'להיות', 'ידי', 'ולא', 'אך', 'בצורה', 'כפי',
    'לפי', 'ביחס', 'עד', 'מאז',
}

ENG_STOP = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'in', 'on', 'at', 'to',
    'for', 'of', 'and', 'or', 'but', 'has', 'have', 'had', 'this', 'that',
    'these', 'those', 'it', 'its', 'be', 'been', 'will', 'would', 'could',
    'should', 'may', 'might', 'from', 'with', 'by', 'as', 'if', 'then',
    'than', 'so', 'not', 'no', 'do', 'did', 'does', 'i', 'we', 'he', 'she',
    'they', 'you', 'my', 'our', 'his', 'her', 'their', 'your', 'can', 'all',
    'said', 'say', 'new', 'after', 'before', 'more', 'also', 'about',
    # URL / tech noise — must NOT appear in keyword extraction
    'https', 'http', 'www', 'com', 'org', 'net', 'gov', 'edu', 'html',
}

HEB_STOP.update({
    # זמן — כלליים מדי לזיהוי נושא
    'בוקר', 'הבוקר', 'הערב', 'ערב', 'לילה', 'הלילה', 'צהריים', 'הצהריים',
    'שעה', 'שעות', 'דקות', 'שניות', 'כעת', 'אחה"צ', 'השעה',
    # פלטפורמות / מדיה — מופיעות בכל הודעה
    'טלגרם', 'ווצאפ', 'וואטסאפ', 'טוויטר', 'פייסבוק', 'אינסטגרם',
    'ערוץ', 'הערוץ', 'ערוצים', 'פוסט', 'ציוץ', 'שידור', 'מקור',
    # כותרות שאינן ספציפיות לנושא
    'ידיעות', 'ידיעה', 'כותרת', 'כותרות', 'מנסרה', 'חדשות', 'בשידור',
    # כמות / מדדים כלליים
    'אחוז', 'אחוזים', 'מיליון', 'מיליארד', 'אלף', 'מאות',
})

# =========================================================
# SPAM PATTERNS (pattern, weight, label)
# =========================================================

SPAM_PATTERNS = [
    # --- פרסומות עברית ---
    # "מבצע" = deal/promo OR military op → trigger only with commercial context
    (r'\bמבצע\b\s*(?=\d|%|₪|\$|ב-\d|ב\d)',  4, 'deal_heb'),
    (r'\bמבצע\s+מ(?:ד|ג|ט|צ)',               3, 'deal_heb2'),
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
    (r'הצטרפ[ו]',                    5, 'join_heb'),
    (r'הירשמ[ו]',                    5, 'register_heb'),
    (r'הרשמ[ו]',                     5, 'signup_heb'),
    (r'עקב[ו]\s*אחרינו',             5, 'follow_us_heb'),
    (r'לינק\s*בתגובה',               5, 'link_in_comment'),
    (r'הצטרפ\S*\s*לקבוצ',           6, 'join_group_heb'),
    (r'הצטרפ\S*\s*לערוץ',           6, 'join_channel_heb'),
    (r'ערוץ\s+חדש',                  4, 'new_channel_heb'),
    (r'לפרטים\s+נוספים\s*:',        4, 'more_details'),
    (r'כל\s+הפרטים\s*:',            4, 'all_details'),
    (r'אל\s+תפספס',                  4, 'dont_miss'),
    (r'זמן\s+מוגבל|הזדמנות\s+אחרונה', 5, 'limited_time'),
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
    # --- CTA אנגלית ---
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
# AD FOOTER PATTERNS (להסרה)
# =========================================================

_AD_FOOTER_RE = [
    re.compile(p, re.IGNORECASE | re.MULTILINE) for p in [
        r'(?:^|\n)[\s\U0001F300-\U0001FAFF]*(?:הצטרפ\S*|עקב\S*|לחצ\S*)\s+ל[^\n]{0,80}',
        r'(?:^|\n)(?:לפרטים|לקישור|לשיתוף)[^\n]{0,60}t\.me[^\n]*',
        r'(?:^|\n)@\w{3,}(?:\s[-\u2013]\s[^\n]{0,50})?',
        r'(?:^|\n)מקור\s*:?\s*@\w+[^\n]*',
        r'(?:^|\n)©[^\n]*',
        r'(?:^|\n)\s*\|\s*@\w+[^\n]*',
        r't\.me/(?:joinchat|\+)\S+',
    ]
]

# =========================================================
# KEYWORD EXTRACTION
# =========================================================

_HEB_PREFIXES = set('לבמכהוש')


def _strip_heb_prefix(word: str) -> str:
    """
    מסיר תחילית עברית חד-אותית אם מתאים: לאשדוד→אשדוד, וטראמפ→טראמפ.
    דורש: מילה ≥5 תווים, והתוצאה ≥5 תווים — מונע חיתוך שמות זרים קצרים
    (למשל: ביידן(5)→יידן(4) לא יחתך, בירושלים(8)→ירושלים(7) יחתך).
    """
    if len(word) >= 5 and word[0] in _HEB_PREFIXES:
        stripped = word[1:]
        if stripped not in HEB_STOP and len(stripped) >= 5:
            return stripped
    return word


_URL_RE = re.compile(r'https?://\S+|www\.\S+')


def extract_keywords(text: str) -> set:
    """
    מחלץ מילות מפתח משמעותיות מטקסט.
    כולל: מספרים, מילים עבריות (+ גרסה ללא תחילית), מילים אנגליות, ציטוטים.
    URLs מוסרים לפני עיבוד כדי למנוע false-positives על 'https', 'com' וכד'.
    """
    if not text:
        return set()

    # הסר URLs לפני הכל
    clean = _URL_RE.sub(' ', text)

    result = set()
    result.update(re.findall(r'\d+', clean))

    for w in re.findall(r'[\u05d0-\u05ea]{3,}', clean):
        if w not in HEB_STOP:
            # Add only the NORMALIZED form to prevent union inflation:
            # "וטראמפ" and "טראמפ" would otherwise both enter the set, doubling
            # the union and halving the Jaccard score for short messages.
            result.add(_strip_heb_prefix(w))

    result.update(
        w.lower() for w in re.findall(r'[A-Za-z]{3,}', clean)
        if w.lower() not in ENG_STOP
    )

    result.update(re.findall(r'"([^"]{2,30})"', clean))
    result.update(re.findall(r"'([^']{2,30})'", clean))

    return result


# =========================================================
# SIMILARITY FUNCTIONS
# =========================================================

def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _text_to_bow(text: str) -> Counter:
    tokens = re.findall(r'[\u05d0-\u05ea]+|[A-Za-z]+|\d+', text.lower())
    return Counter(t for t in tokens if len(t) >= 2)


def _cosine(c1: Counter, c2: Counter) -> float:
    if not c1 or not c2:
        return 0.0
    shared = set(c1) & set(c2)
    num = sum(c1[k] * c2[k] for k in shared)
    mag = math.sqrt(sum(v * v for v in c1.values())) * math.sqrt(sum(v * v for v in c2.values()))
    return num / mag if mag else 0.0


def _levenshtein_ratio(s1: str, s2: str) -> float:
    s1, s2 = s1[:200], s2[:200]
    if not s1 or not s2:
        return 0.0
    if abs(len(s1) - len(s2)) / max(len(s1), len(s2), 1) > 0.5:
        return 0.0
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return 1 - prev[-1] / max(len(s1), len(s2))


# =========================================================
# SPAM DETECTION
# =========================================================

def compute_spam_score(text: str) -> tuple:
    """
    מחזיר (score, [(label, weight), ...]).
    score ≥ SPAM_SCORE_THRESHOLD → ספאם.
    """
    if not text:
        return 0, []

    score = 0
    matched = []

    for pattern, weight, label in SPAM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            matched.append((label, weight))

    emoji_count = len(re.findall(
        r'[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\U0001FA00-\U0001FA6F]',
        text
    ))
    words = max(len(text.split()), 1)
    er = emoji_count / words
    if er > 0.35:
        score += 7
        matched.append(('emoji_high', 7))
    elif er > 0.18:
        score += 3
        matched.append(('emoji_moderate', 3))

    exc = text.count('!')
    if exc >= 4:
        score += min(exc, 6)
        matched.append((f'exclamation_x{exc}', min(exc, 6)))

    urls = len(re.findall(r'https?://', text))
    if urls >= 3:
        score += 5
        matched.append(('many_urls', 5))
    elif urls == 2:
        score += 2
        matched.append(('two_urls', 2))

    words_list = text.split()
    if len(words_list) >= 12:
        fourgrams = [' '.join(words_list[i:i+4]) for i in range(len(words_list) - 3)]
        if any(c >= 2 for c in Counter(fourgrams).values()):
            score += 4
            matched.append(('repeated_phrase', 4))

    return score, matched


# =========================================================
# AD FOOTER CLEANER
# =========================================================

def clean_ad_footer(text: str) -> str:
    """
    מנסה להסיר שורות פרסומיות בסוף ההודעה (לינקי ערוץ, CTA, copyright).
    אם הניקוי מוריד >60% מהתוכן — מחזיר את המקורי.
    """
    if not text:
        return text
    cleaned = text
    for pattern in _AD_FOOTER_RE:
        cleaned = pattern.sub('', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    if len(cleaned) < len(text) * 0.4:
        return text
    return cleaned


# =========================================================
# KEYWORD BURST DETECTION
# =========================================================

def keyword_burst_check(text: str, recent_texts: list) -> tuple:
    """
    מחזיר (is_burst, [(keyword, count), ...]).
    דורש לפחות 2 מילות-מפתח שחוזרות יחד מעל הסף —
    מונע false-positives כשמילה אחת כללית (כמו "איראן") חוזרת בידיעות שונות.
    """
    if len(text) < MIN_LEN_FOR_DEDUP:
        return False, []

    curr_kw = extract_keywords(text)
    if not curr_kw:
        return False, []

    hits = Counter()
    for recent in recent_texts[-BURST_WINDOW:]:
        if recent:
            hits.update(curr_kw & extract_keywords(recent))

    bursting = [(kw, cnt) for kw, cnt in hits.most_common(8) if cnt >= BURST_THRESHOLD]
    if len(bursting) < 2:
        return False, []
    return True, bursting


# =========================================================
# MAIN ENGINE
# =========================================================

class LocalDedupEngine:
    """
    מנוע ניכוי כפילויות וסינון ספאם מקומי — ללא AI, ללא עלות.

    check(text, recent_texts) → dict:
        action:        'BLOCK_SPAM' | 'BLOCK_DUP' | 'PASS'
        cleaned_text:  הטקסט לאחר ניקוי (יכול להיות קצר יותר)
        details:       מידע לדיבאג
    """

    def check(self, text: str, recent_texts: list) -> dict:
        if not text or len(text.strip()) < 5:
            return {'action': 'PASS', 'cleaned_text': text, 'details': {}}

        details = {}

        # ---- שלב 1: בדיקת ספאם ----
        spam_score, spam_matches = compute_spam_score(text)
        details['spam_score'] = spam_score
        details['spam_patterns'] = spam_matches

        if spam_score >= SPAM_SCORE_THRESHOLD:
            return {
                'action': 'BLOCK_SPAM',
                'cleaned_text': text,
                'details': details,
            }

        # ---- שלב 2: בדיקת כפילות ----
        is_dup = False
        dup_reason = None

        if recent_texts and len(text) >= MIN_LEN_FOR_DEDUP:

            # A. Keyword Jaccard
            # דורש לפחות 2 מילות-מפתח משותפות — מונע false-positive כשרק שם אחד משותף.
            # (למשל: "נתניהו הגיב" ו-"נתניהו עוזב" חולקים רק נתניהו → לא כפילות)
            curr_kw = extract_keywords(text)
            if len(curr_kw) >= 3:
                best_jac = 0.0
                for _r in recent_texts:
                    if _r:
                        _okw = extract_keywords(_r)
                        _shared = curr_kw & _okw
                        if len(_shared) >= 2:   # minimum 2 overlapping keywords
                            _union = len(curr_kw) + len(_okw) - len(_shared)
                            _jac = len(_shared) / _union if _union else 0.0
                            if _jac > best_jac:
                                best_jac = _jac
                details['keyword_jaccard'] = best_jac
                if best_jac >= KEYWORD_JACCARD_THRESHOLD:
                    is_dup = True
                    dup_reason = f'keyword_jaccard({best_jac:.2f})'

            if not is_dup:
                # B. Cosine similarity
                curr_bow = _text_to_bow(text)
                best_cos = max(
                    (_cosine(curr_bow, _text_to_bow(r)) for r in recent_texts if r),
                    default=0.0
                )
                details['cosine'] = best_cos
                if best_cos >= COSINE_THRESHOLD:
                    is_dup = True
                    dup_reason = f'cosine({best_cos:.2f})'

            if not is_dup and len(text) <= 300:
                # C. Edit distance (רק להודעות קצרות)
                best_ed = max(
                    (_levenshtein_ratio(text, r) for r in recent_texts if r and len(r) <= 350),
                    default=0.0
                )
                details['edit_distance'] = best_ed
                if best_ed >= 0.88:
                    is_dup = True
                    dup_reason = f'edit_distance({best_ed:.2f})'

            if not is_dup:
                # D. Keyword burst
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
