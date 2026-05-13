"""
main.py — bot_news
==================
Telegram news aggregator with aggressive algorithmic deduplication.
Zero AI calls. Zero cost.
"""

import asyncio
import hashlib
import json
import os
import sys
import sqlite3
import re
import time
import atexit
import signal
import tempfile
from collections import defaultdict
from io import BytesIO

try:
    from PIL import Image
    import imagehash
    _PHASH_AVAILABLE = True
except ImportError:
    _PHASH_AVAILABLE = False

from telethon import TelegramClient, events
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import MessageMediaWebPage, DocumentAttributeVideo
from simhash import Simhash
from deep_translator import GoogleTranslator
from langdetect import detect

import config
from ai_manager import ai_manager
from dedup_engine import LocalDedupEngine

import logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_LOCK_HANDLE = None


def _release_single_instance_lock() -> None:
    global _LOCK_HANDLE
    if not _LOCK_HANDLE:
        return
    try:
        if sys.platform == "win32":
            _LOCK_HANDLE.seek(0)
            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(_LOCK_HANDLE, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        _LOCK_HANDLE.close()
    except OSError:
        pass
    _LOCK_HANDLE = None


def acquire_single_instance_lock(lock_name: str = "bot_news_main.lock"):
    lock_path = os.path.join(tempfile.gettempdir(), lock_name)
    lock = open(lock_path, "a+", encoding="utf-8")
    lock.seek(0)
    lock.write("0")
    lock.flush()
    lock.seek(0)
    try:
        if sys.platform == "win32":
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.error("❌ bot_news כבר רץ — יוצא.")
        lock.close()
        sys.exit(1)

    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    global _LOCK_HANDLE
    _LOCK_HANDLE = lock
    atexit.register(_release_single_instance_lock)
    return lock

# =========================================================
# IN-MEMORY BUFFER
# Fixes race condition: when 8 channels post the same story
# within milliseconds, async handlers run interleaved.
# Adding to buffer IMMEDIATELY lets the next handler see it
# even before the DB write completes.
# =========================================================

_buffer: list = []   # list of (text, simhash_int_or_None)
_BUFFER_MAX = 300    # keep last 300 entries in memory


def _buffer_add(text: str, sh_val):
    _buffer.append((text, sh_val))
    if len(_buffer) > _BUFFER_MAX:
        _buffer.pop(0)


# =========================================================
# ALERT FLOOD DETECTOR
# Detects Pikud HaOref style floods (sirens, rockets, etc.)
# and blocks the wave of messages + media.
# People are already connected to the official app.
# =========================================================

_ALERT_KW = {
    'אזעקה', 'כניסת כלי טיס', 'חדירת כלי טיס', 'כלי טיס',
    'רקטה', 'טיל', 'טילים', 'רקטות', 'ירי רקטות', 'שיגור',
    'כיפת ברזל', 'יירוט', 'יירטה', 'פיקוד העורף',
    'היכנסו לממד', 'מרחב מוגן', 'אדום', 'ירי',
    'rocket', 'missile', 'siren', 'iron dome', 'red alert',
}


class AlertFloodDetector:
    """
    When THRESHOLD alert-messages arrive within WINDOW_SEC seconds
    → enter flood-block mode for BLOCK_SEC seconds.
    During block mode, alert-related messages AND their media are dropped.
    """
    THRESHOLD = 4
    WINDOW_SEC = 180    # 3 minutes
    BLOCK_SEC = 720     # block for 12 minutes

    def __init__(self):
        self._hits: list = []
        self._blocked_until: float = 0.0

    def _is_alert(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in _ALERT_KW)

    def check(self, text: str) -> bool:
        """
        Returns True if message should be blocked as alert flood.
        Also updates internal hit counter.
        """
        now = time.time()
        is_alert = self._is_alert(text)

        if is_alert:
            self._hits = [t for t in self._hits if now - t < self.WINDOW_SEC]
            self._hits.append(now)
            if len(self._hits) >= self.THRESHOLD:
                self._blocked_until = now + self.BLOCK_SEC
                logger.info(f"🚨 Alert flood detected ({len(self._hits)} msgs). Blocking for {self.BLOCK_SEC}s.")
                return True

        if now < self._blocked_until and is_alert:
            return True

        return False

    @property
    def is_active(self) -> bool:
        return time.time() < self._blocked_until


# =========================================================
# ENTITY REGISTRY
# Tracks named entities from passing messages.
# If 2+ key entities from a new message were each seen 3+ times
# in the last 45 minutes → duplicate topic flood.
# =========================================================

class EntityRegistry:
    WINDOW_MIN = 45
    MIN_COUNT = 1    # entity must appear this many times to count (1 = block on repeat)
    MIN_MATCH = 2    # need this many matching entities

    def __init__(self):
        self._reg: dict = defaultdict(list)

    def _clean(self):
        cutoff = time.time() - self.WINDOW_MIN * 60
        dead = [e for e, ts in self._reg.items() if not ts or max(ts) < cutoff]
        for e in dead:
            del self._reg[e]
        for e in self._reg:
            self._reg[e] = [t for t in self._reg[e] if t > cutoff]

    def register(self, entities: set):
        now = time.time()
        for e in entities:
            self._reg[e].append(now)

    def is_flood(self, entities: set) -> tuple:
        self._clean()
        flood = [(e, len(self._reg[e])) for e in entities
                 if len(self._reg.get(e, [])) >= self.MIN_COUNT]
        return len(flood) >= self.MIN_MATCH, flood


# =========================================================
# GLOBAL INSTANCES
# =========================================================

_local_dedup = LocalDedupEngine()
_alert = AlertFloodDetector()
_entities = EntityRegistry()

# =========================================================
# KEYWORD ALERT
# כשמופיעות מילות מפתח חשובות בהודעה → שליחה לערוץ חדשות מרוכזות
# =========================================================

import requests

ALERT_KEYWORDS = {
    'שיגור', 'יציאות', 'יציאה', 'פוליגון', 'למרכז'
}

# ID האדמין שמקבל התראה אישית
ALERT_ADMIN_ID = getattr(config, "ALERT_ADMIN_ID", 165270683)
ALERT_CACHE_TTL_SEC = 7 * 24 * 60 * 60
ALERT_CONFIDENCE_THRESHOLD = 0.78

ALERT_STRONG_PATTERNS = [
    re.compile(r"שיגור(?:ים)?\s+ל(?:מרכז|גוש\s*דן|אזור\s+המרכז|צפון|עפולה)", re.IGNORECASE),
    re.compile(r"שיגורים?\s+ל(?:מרכז|גוש\s*דן|אזור\s+המרכז|צפון|עפולה)", re.IGNORECASE),
    re.compile(r"ירי\s+(?:רקטות?|טילים?)\s+ל(?:מרכז|צפון|עפולה|גוש\s*דן)", re.IGNORECASE),
    re.compile(r"אזעקות?\s+ב(?:מרכז|צפון|עפולה|גוש\s*דן)", re.IGNORECASE),
]

ALERT_WEAK_TERMS = {
    "שיגור", "שיגורים", "שוגר", "שוגרו", "טיל", "טילים",
    "רקטה", "רקטות", "ירי", "אזעקה", "אזעקות", "יירוט",
    "מרכז", "צפון", "עפולה", "גוש דן", "הגליל", "חיפה",
}


def _personal_alerts_enabled() -> bool:
    return bool(getattr(config, "ENABLE_PERSONAL_ALERTS", False))


def _personal_alert_bot_token() -> str:
    return str(getattr(config, "ALERT_BOT_TOKEN", "")).strip()


async def send_keyword_alert(text: str, source_title: str, source_link: str | None):
    """
    שולח התראה בולטת לערוץ חדשות מרוכזות + הודעה אישית לאדמין דרך הבוט.
    """
    try:
        if not _personal_alerts_enabled():
            return

        # Debug print
        logger.info(f"[ALERT CHECK] Checking text (length {len(text)}) for keywords...")
        
        # Make search robust (lowercase, though Hebrew has no case, it's safe)
        text_for_search = text.lower()
        triggered = [kw for kw in ALERT_KEYWORDS if kw in text_for_search]
        
        if not triggered:
            # Not found. Return silently to avoid log spam, but we can log at debug level if needed.
            return

        logger.info(f"[ALERT CHECK] -> TRIGGERED! Keywords found: {triggered}")

        kw_list = ' | '.join(f'**{kw}**' for kw in triggered)
        footer = (f"[{source_title}]({source_link})"
                  if source_link else f"**{source_title}**")

        alert_msg = (
            f"🚨 **התראת מילת מפתח** — {kw_list}\n"
            f"{'─' * 30}\n"
            f"{text}\n\n"
            f"מקור: {footer}"
        )

        # שליחה אישית לאדמין דרך הבוט הפעיל (API של טלגרם) - מגיע מיד לפרטי בבוט
        # (לא שולחים את ההתראה לערוץ החדשות — המידע מגיע לשם ממילא)
        bot_token = _personal_alert_bot_token()
        if bot_token:
            try:
                # We don't use Markdown here because raw news text might contain unescaped *, _, [, etc.
                # which causes Telegram API to reject the whole message with 400 Bad Request.
                clean_kw = str(kw_list).replace('**', '')
                clean_footer = str(footer).replace('**', '').replace('[', '').replace(']', '').replace('(', ' - ').replace(')', '')
                
                personal_msg = (
                    f"🚨 התראה חיה מערוצי החדשות 🚨\n"
                    f"מילות מפתח: {clean_kw}\n"
                    f"──────────────────────────────\n"
                    f"{text[:800]}{'...' if len(text) > 800 else ''}\n\n"
                    f"מקור: {clean_footer}"
                )
                
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {
                    "chat_id": ALERT_ADMIN_ID,
                    "text": personal_msg,
                    "disable_web_page_preview": True,
                }
                logger.info(f"[ALERT CHECK] Making HTTP POST to bot API for admin {ALERT_ADMIN_ID}...")
                response = requests.post(url, json=payload, timeout=5)
                if response.ok:
                    logger.info(f"[KEYWORD ALERT] Sent directly to bot for admin {ALERT_ADMIN_ID}")
                else:
                    logger.warning(f"[KEYWORD ALERT] Failed sending to bot: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"[KEYWORD ALERT] Error dialing bot API: {e}")
        else:
            logger.error("[KEYWORD ALERT] NO BOT TOKEN FOUND!")
    except Exception as e:
        logger.error(f"[CRITICAL ALERT ERROR] {e}", exc_info=True)

# =========================================================
# SECURITY ALERT CLASSIFIER
# Uses rule-based gating first, then AIManager cascade only
# for ambiguous messages that mention launch/security terms.
# =========================================================

def _alert_cache_key(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _load_cached_alert(text: str):
    cache_key = _alert_cache_key(text)
    cutoff = time.time() - ALERT_CACHE_TTL_SEC
    try:
        with sqlite3.connect(config.DB_FILE) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT result, timestamp FROM ai_moderation_cache WHERE text_hash = ?",
                (cache_key,),
            )
            row = c.fetchone()
        if not row:
            return None
        result_json, ts = row
        if ts and ts < cutoff:
            return None
        if not result_json:
            return None
        return json.loads(result_json) if isinstance(result_json, str) else result_json
    except Exception as e:
        logger.debug(f"[ALERT CACHE] load failed: {e}")
        return None


def _store_cached_alert(text: str, result: dict) -> None:
    cache_key = _alert_cache_key(text)
    try:
        with sqlite3.connect(config.DB_FILE) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO ai_moderation_cache (text_hash, result, timestamp) VALUES (?, ?, ?)",
                (cache_key, json.dumps(result, ensure_ascii=False), time.time()),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"[ALERT CACHE] store failed: {e}")


def _rule_based_security_alert(text: str):
    normalized = (text or "").lower()

    for pattern in ALERT_STRONG_PATTERNS:
        if pattern.search(normalized):
            region = "center" if ("מרכז" in normalized or "גוש דן" in normalized or "תל אביב" in normalized) else "north"
            if "עפולה" in normalized:
                region = "afula"
            return {"is_alert": True, "confidence": 0.99, "region": region, "reason": "strong_pattern", "source": "rule"}

    launch_terms = any(term in normalized for term in ("שיגור", "שיגורים", "שוגר", "שוגרו", "טיל", "טילים", "רקטה", "רקטות", "ירי", "אזעקה", "אזעקות", "יירוט"))
    center_terms = any(term in normalized for term in ("מרכז", "גוש דן", "תל אביב", "השרון"))
    north_terms = any(term in normalized for term in ("צפון", "עפולה", "חיפה", "הגליל", "קריות", "עמק יזרעאל"))

    if launch_terms and (center_terms or north_terms):
        region = "center" if center_terms else "north"
        if "עפולה" in normalized:
            region = "afula"
        return {"is_alert": True, "confidence": 0.96, "region": region, "reason": "launch_with_region", "source": "rule"}

    if not any(term in normalized for term in ALERT_WEAK_TERMS):
        return {"is_alert": False, "confidence": 0.0, "region": "unknown", "reason": "no_security_signal", "source": "rule"}

    if "מרכז" in normalized and not launch_terms:
        return {"is_alert": False, "confidence": 0.08, "region": "center", "reason": "center_word_only", "source": "rule"}

    if "צפון" in normalized and not launch_terms:
        return {"is_alert": False, "confidence": 0.08, "region": "north", "reason": "north_word_only", "source": "rule"}

    if "שיגור" in normalized or "שיגורים" in normalized:
        return None

    return {"is_alert": False, "confidence": 0.15, "region": "unknown", "reason": "insufficient_context", "source": "rule"}


def _parse_ai_alert_result(raw: str):
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def classify_security_alert(text: str, source_title: str) -> dict:
    cached = _load_cached_alert(text)
    if cached:
        return cached

    rule_result = _rule_based_security_alert(text)
    if rule_result is not None and rule_result.get("source") == "rule" and rule_result.get("is_alert") is not None:
        if rule_result.get("reason") not in {"no_security_signal", "insufficient_context"}:
            _store_cached_alert(text, rule_result)
            return rule_result
        if rule_result.get("is_alert") is False:
            _store_cached_alert(text, rule_result)
            return rule_result

    prompt = f"""
You classify Hebrew news alerts for a public warning bot.
Return JSON only, no markdown, no explanation.

Schema:
{{
  "is_alert": true/false,
  "confidence": 0.0-1.0,
  "region": "center|north|afula|south|other|unknown",
  "reason": "short reason"
}}

Rules:
- Return true only when the text is actually about rockets, missiles, launches, sirens, interceptions, or an official security warning.
- "שיגורים למרכז" => true.
- "שיגורים לצפון" => true.
- If north is mentioned and the Afula area is included or implied, treat it as true.
- A single word like "שיגור" or "מרכז" with no security context => false.
- If unsure => false.

Source title: {source_title}
Text:
{text}
""".strip()

    result = None
    try:
        raw = await ai_manager.chat_completion(prompt, temperature=0.0)
        result = _parse_ai_alert_result(raw)
    except Exception as e:
        logger.debug(f"[ALERT AI] classifier failed: {e}")

    if not result:
        result = {
            "is_alert": False,
            "confidence": 0.0,
            "region": "unknown",
            "reason": "ai_unavailable_or_unparsed",
            "source": "ai",
        }
    else:
        result.setdefault("source", "ai")
        result.setdefault("region", "unknown")
        result.setdefault("reason", "ai_result")
        try:
            result["confidence"] = float(result.get("confidence", 0.0))
        except Exception:
            result["confidence"] = 0.0
        result["is_alert"] = bool(result.get("is_alert")) and result["confidence"] >= ALERT_CONFIDENCE_THRESHOLD

    _store_cached_alert(text, result)
    return result


async def send_security_alert(text: str, source_title: str, source_link: str | None):
    """
    Sends a Telegram alert only for real missile/rocket/security events.
    """
    try:
        if not _personal_alerts_enabled():
            return

        logger.info(f"[ALERT CHECK] Checking text (length {len(text)}) for security alert relevance...")
        result = await classify_security_alert(text, source_title)
        if not result.get("is_alert"):
            logger.info(f"[ALERT CHECK] skipped: {result.get('reason')} (confidence={result.get('confidence', 0):.2f})")
            return

        region = result.get("region", "unknown")
        confidence = float(result.get("confidence", 0.0))
        footer = (f"[{source_title}]({source_link})" if source_link else f"**{source_title}**")
        alert_msg = (
            f"🚨 **התראת ביטחון** — region={region} | confidence={confidence:.2f}\n"
            f"{'—' * 30}\n"
            f"{text[:1000]}{'...' if len(text) > 1000 else ''}\n\n"
            f"מקור: {footer}\n"
            f"reason: {result.get('reason', 'n/a')}"
        )

        bot_token = _personal_alert_bot_token()
        if not bot_token:
            logger.error("[KEYWORD ALERT] NO BOT TOKEN FOUND!")
            return

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": ALERT_ADMIN_ID,
            "text": alert_msg,
            "disable_web_page_preview": True,
        }
        logger.info(f"[ALERT CHECK] Sending alert to admin {ALERT_ADMIN_ID}...")
        response = requests.post(url, json=payload, timeout=5)
        if response.ok:
            logger.info(f"[KEYWORD ALERT] Sent directly to bot for admin {ALERT_ADMIN_ID}")
        else:
            logger.warning(f"[KEYWORD ALERT] Failed sending to bot: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"[CRITICAL ALERT ERROR] {e}", exc_info=True)

# =========================================================
# PERCEPTUAL HASH (pHash) — visual image dedup
# Catches same image re-compressed, watermarked, or cropped.
# Hamming distance ≤ PHASH_THRESHOLD → visually identical.
# =========================================================

PHASH_THRESHOLD = 12  # out of 64 bits; ≤12 = ~81% similarity


async def check_and_store_phash(message) -> bool:
    """
    Downloads image, computes pHash, compares against recent hashes.
    Returns True if image is a visual duplicate.
    Skips silently if imagehash is not installed or download fails.
    """
    if not _PHASH_AVAILABLE:
        return False
    if not message or not message.media or not hasattr(message.media, 'photo'):
        return False

    # Skip images larger than 5MB to avoid blocking on large downloads
    photo = message.media.photo
    sizes = getattr(photo, 'sizes', [])
    for s in sizes:
        if getattr(s, 'size', 0) > 5 * 1024 * 1024:
            return False

    try:
        img_bytes = await client.download_media(message, bytes)
        if not img_bytes:
            return False
        img = Image.open(BytesIO(img_bytes)).convert('RGB')
        phash_val = str(imagehash.phash(img))
    except Exception as e:
        logger.debug(f"[PHASH] compute failed: {e}")
        return False

    cutoff = time.time() - (config.WINDOW_HOURS * 3600)
    try:
        with sqlite3.connect(config.DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT phash FROM photo_phashes WHERE timestamp > ?", (cutoff,))
            recent_hashes = [row[0] for row in c.fetchall()]

        h1 = imagehash.hex_to_hash(phash_val)
        for rh in recent_hashes:
            try:
                if h1 - imagehash.hex_to_hash(rh) <= PHASH_THRESHOLD:
                    logger.info(f"[PHASH] visual duplicate detected (dist≤{PHASH_THRESHOLD})")
                    return True
            except Exception:
                continue

        # Not a duplicate — store
        with sqlite3.connect(config.DB_FILE) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO photo_phashes (phash, timestamp) VALUES (?, ?)",
                      (phash_val, time.time()))
            conn.commit()
    except Exception as e:
        logger.debug(f"[PHASH] DB error: {e}")

    return False


# =========================================================
# DATABASE
# =========================================================

def init_db():
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS messages
                     (hash TEXT, timestamp REAL, source_id INTEGER,
                      media_id TEXT, message_text TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS channel_map
                     (source_id INTEGER PRIMARY KEY, target_id INTEGER, title TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS categories
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT, channel_id INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_moderation_cache
                     (text_hash TEXT PRIMARY KEY, result TEXT, timestamp REAL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS photo_phashes
                     (phash TEXT, timestamp REAL)''')

        # Sync categories from config
        logger.info("Syncing categories from config...")
        defaults = [(name, cid) for name, cid in config.TARGETS.items() if cid]
        for name, cid in defaults:
            c.execute("SELECT id FROM categories WHERE name = ?", (name,))
            row = c.fetchone()
            if row:
                c.execute("UPDATE categories SET channel_id = ? WHERE id = ?", (cid, row[0]))
            else:
                c.execute("INSERT INTO categories (name, channel_id) VALUES (?, ?)", (name, cid))
        conn.commit()

        # Ensure columns exist
        for col in ["message_text TEXT", "media_id TEXT"]:
            try:
                c.execute(f"ALTER TABLE messages ADD COLUMN {col}")
            except Exception:
                pass

        # Fix stale target_ids in channel_map
        c.execute("SELECT id, name, channel_id FROM categories")
        cats = c.fetchall()
        valid_ids = {row[2] for row in cats if row[2] is not None}
        preferred_id = next(
            (ch for _, n, ch in cats if n == 'news' and ch), None
        ) or (next(iter(valid_ids)) if valid_ids else None)

        if preferred_id:
            c.execute("SELECT source_id, target_id FROM channel_map")
            for src, tgt in c.fetchall():
                if tgt != -1 and tgt not in valid_ids:
                    c.execute("UPDATE channel_map SET target_id=? WHERE source_id=?",
                              (preferred_id, src))
        conn.commit()


# =========================================================
# CATEGORY HELPERS
# =========================================================

def get_all_categories():
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, channel_id FROM categories ORDER BY id ASC")
        return c.fetchall()


def get_category_by_id(cat_db_id):
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT channel_id, name FROM categories WHERE id = ?", (cat_db_id,))
        return c.fetchone()


def add_new_category(name, channel_id):
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO categories (name, channel_id) VALUES (?, ?)", (name, channel_id))
        new_id = c.lastrowid
        conn.commit()
    return new_id


# =========================================================
# CHANNEL MAPPING
# =========================================================

def get_target_channel(source_id):
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT target_id FROM channel_map WHERE source_id = ?", (source_id,))
        result = c.fetchone()
        if not result:
            try:
                clean_id = int(str(source_id).replace("-100", ""))
                c.execute("SELECT target_id FROM channel_map WHERE source_id = ?", (clean_id,))
                result = c.fetchone()
            except Exception:
                pass
    return result[0] if result else None


def save_mapping(source_id, target_id, title):
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO channel_map VALUES (?, ?, ?)",
                  (source_id, target_id, title))
        conn.commit()


# =========================================================
# SIMHASH HELPERS
# =========================================================

def get_simhash(text):
    if not text:
        return None
    clean = re.sub(r'http\S+', '', text)
    clean = re.sub(r'[^\w\s]', '', clean).lower().strip()
    if not clean:
        return None
    features = [clean[i:i+3] for i in range(len(clean) - 2)]
    features.extend(clean.split())
    return Simhash(features)


# =========================================================
# MEDIA HELPERS
# =========================================================

def get_media_signature(message):
    """Exact Telegram file-ID based signature."""
    if not message or not message.media:
        return None
    if hasattr(message.media, 'photo'):
        return f"photo_{message.media.photo.id}"
    if hasattr(message.media, 'document'):
        return f"doc_{message.media.document.id}"
    return None


def get_video_metadata(message) -> str | None:
    if not message or not message.media:
        return None
    doc = getattr(message.media, 'document', None)
    if not doc:
        return None
    size = getattr(doc, 'size', None)
    duration = None
    for attr in getattr(doc, 'attributes', []) or []:
        if isinstance(attr, DocumentAttributeVideo):
            duration = getattr(attr, 'duration', None)
            break
    if size is not None and duration is not None:
        # Bucket size to nearest 500KB — catches re-encoded copies of same video
        size_bucket = size // (500 * 1024)
        return f"vid_{duration}_{size_bucket}"
    return None


def is_video_meta_duplicate(vid_meta, source_id) -> bool:
    if not vid_meta:
        return False
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        cutoff = time.time() - (config.WINDOW_HOURS * 3600)
        c.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM messages WHERE media_id = ?", (vid_meta,))
        if c.fetchone()[0] > 0:
            return True
        c.execute(
            "INSERT INTO messages (hash, timestamp, source_id, media_id, message_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (None, time.time(), source_id, vid_meta, None),
        )
        conn.commit()
    return False


# =========================================================
# RECENT MESSAGES: DB + in-memory buffer combined
# =========================================================

def get_recent_rows(limit: int = 120) -> list:
    """
    Returns [(text, simhash_int_or_None)] from buffer + DB.
    Buffer entries come first so concurrent handlers see each other
    without waiting for a DB write.
    """
    cutoff = time.time() - (config.WINDOW_HOURS * 3600)

    # Buffer entries (newest first)
    buf = list(reversed(_buffer))

    # DB entries
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT message_text, hash
            FROM messages
            WHERE timestamp >= ?
              AND message_text IS NOT NULL
              AND TRIM(message_text) != ''
            ORDER BY timestamp DESC
            LIMIT ?
        """, (cutoff, limit))
        db_rows = c.fetchall()

    # Merge, de-duplicate by text
    seen = set()
    combined = []
    for text, sh in buf + db_rows:
        if text and text not in seen:
            seen.add(text)
            combined.append((text, sh))
    return combined[:limit]


# =========================================================
# SAVE MESSAGE RECORD
# =========================================================

def save_message_record(text, message_obj, source_id):
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        cutoff = time.time() - (config.WINDOW_HOURS * 3600)
        c.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
        conn.commit()

        current_media_id = get_media_signature(message_obj)
        current_hash = get_simhash(text)
        hash_val = str(current_hash.value) if current_hash else None

        c.execute(
            "INSERT INTO messages (hash, timestamp, source_id, media_id, message_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (hash_val, time.time(), source_id, current_media_id, text),
        )
        conn.commit()


# =========================================================
# UNIFIED DEDUP PIPELINE
# Called by both main_handler and handle_album_event.
# Returns 'PASS', 'BLOCK_DUP', or 'BLOCK_SPAM'.
# On PASS, also returns cleaned text.
# =========================================================

def run_dedup(text: str, message_obj, source_id: int) -> dict:
    """
    Full dedup pipeline:
      0. BAD_WORDS (inline before calling this)
      1. Alert flood check
      2. Video / exact media duplicate
      3. Photo dimension duplicate
      4. In-memory buffer + DB (Simhash)
      5. LocalDedupEngine (Jaccard / cosine / burst / spam)
      6. Entity registry
    Returns {'action': ..., 'cleaned_text': ..., 'reason': ...}
    """
    # --- 1. Alert flood ---
    if text and _alert.check(text):
        return {'action': 'BLOCK_FLOOD', 'cleaned_text': text,
                'reason': 'alert_flood'}

    # Also block media during alert flood (regardless of text)
    if _alert.is_active and message_obj and message_obj.media:
        if not isinstance(getattr(message_obj, 'media', None), MessageMediaWebPage):
            return {'action': 'BLOCK_FLOOD', 'cleaned_text': text,
                    'reason': 'alert_flood_media'}

    # --- 2. Video metadata exact duplicate ---
    vid_meta = get_video_metadata(message_obj)
    if vid_meta and is_video_meta_duplicate(vid_meta, source_id):
        return {'action': 'BLOCK_DUP', 'cleaned_text': text,
                'reason': 'video_meta_dup'}

    # --- 3. Photo exact file-ID duplicate ---
    photo_exact = get_media_signature(message_obj)
    if photo_exact and photo_exact.startswith('photo_'):
        with sqlite3.connect(config.DB_FILE) as conn:
            c = conn.cursor()
            cutoff = time.time() - (config.WINDOW_HOURS * 3600)
            c.execute("SELECT COUNT(*) FROM messages WHERE media_id=? AND timestamp>?",
                      (photo_exact, cutoff))
            if c.fetchone()[0] > 0:
                return {'action': 'BLOCK_DUP', 'cleaned_text': text,
                        'reason': 'photo_exact_dup'}

    # --- 4. Simhash against buffer + DB ---
    recent_rows = get_recent_rows(limit=120)
    recent_texts = [t for t, _ in recent_rows]

    current_hash = get_simhash(text)
    if current_hash:
        for r_text, r_hash in recent_rows:
            if r_hash and current_hash.distance(Simhash(int(r_hash))) <= config.SIMHASH_THRESHOLD:
                return {'action': 'BLOCK_DUP', 'cleaned_text': text,
                        'reason': f'simhash_dist={current_hash.distance(Simhash(int(r_hash)))}'}

    # --- 5. LocalDedupEngine (Jaccard / cosine / burst / spam) ---
    result = _local_dedup.check(text, recent_texts)
    if result['action'] in ('BLOCK_DUP', 'BLOCK_SPAM'):
        reason = result['details'].get('duplicate_reason') or result['action']
        return {'action': result['action'], 'cleaned_text': text, 'reason': reason}

    cleaned_text = result['cleaned_text'] or text

    # --- 6. Entity registry ---
    from dedup_engine import extract_keywords
    entities = extract_keywords(cleaned_text)
    is_flood, flood_ents = _entities.is_flood(entities)
    if is_flood:
        return {'action': 'BLOCK_DUP', 'cleaned_text': cleaned_text,
                'reason': f'entity_flood({flood_ents[:2]})'}

    # ✅ PASS — register and add to buffer
    _entities.register(entities)
    sh_val = current_hash.value if current_hash else None
    _buffer_add(cleaned_text, sh_val)

    return {'action': 'PASS', 'cleaned_text': cleaned_text, 'reason': None}


# =========================================================
# TRANSLATION
# =========================================================

def translate_to_hebrew(text):
    if not text or len(text.strip()) < 3:
        return None
    try:
        lang = detect(text)
        if lang == 'en':
            return GoogleTranslator(source='auto', target='iw').translate(text)
        return None
    except Exception as e:
        logger.warning(f"Translation error: {e}")
        return None


# =========================================================
# TELEGRAM CLIENT
# =========================================================

client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)

# =========================================================
# ALBUM CACHE
# =========================================================

album_cache: dict = {}


async def handle_album_event(event):
    """Collects album messages, then runs full dedup on the caption."""
    global album_cache
    gid = event.grouped_id
    if gid is None:
        return

    if gid not in album_cache:
        album_cache[gid] = [event.message]
        await asyncio.sleep(3.0)
        messages = album_cache.pop(gid, [])
        if not messages:
            return

        source_id = event.chat_id
        target_id = get_target_channel(source_id)
        if not target_id or target_id == -1:
            return

        # Extract caption
        text = next((m.message for m in messages if m.message and m.message.strip()), "")

        # BAD_WORDS
        text_lower = text.lower()
        for word in config.BAD_WORDS:
            if word.lower() in text_lower:
                logger.info(f"[ALBUM] BAD_WORD '{word}'")
                return

        # Full dedup pipeline (pass first message as representative)
        result = run_dedup(text, messages[0], source_id)
        if result['action'] != 'PASS':
            logger.info(f"[ALBUM] BLOCKED: {result['action']} — {result['reason']}")
            return

        text = result['cleaned_text']

        # --- pHash: בדיקת כפילות ויזואלית ---
        if await check_and_store_phash(messages[0]):
            logger.info("[ALBUM] BLOCKED: photo_phash_dup")
            return

        save_message_record(text, messages[0], source_id)

        translation = translate_to_hebrew(text)
        final_message = text
        if translation:
            final_message += f"\n\n--- תרגום ---\n{translation}"

        source_title = event.chat.title or str(source_id)
        source_link = (f"https://t.me/{event.chat.username}"
                       if event.chat.username else None)
        footer = (f"\n\nמקור: [{source_title}]({source_link})"
                  if source_link else f"\n\nמקור: **{source_title}**")
        final_message += footer

        # --- בדיקת מילות מפתח → התראה לערוץ חדשות מרוכזות ---
        await send_keyword_alert(text, source_title, source_link)

        files = [m.media for m in messages
                 if m.media and not isinstance(m.media, MessageMediaWebPage)]

        try:
            await client.send_file(target_id, file=files,
                                   caption=final_message, link_preview=False)
            logger.info(f"[ALBUM] Sent to {target_id}")
        except Exception as e:
            logger.warning(f"[ALBUM] Send error: {e}")
    else:
        album_cache[gid].append(event.message)


# =========================================================
# ADMIN COMMANDS
# =========================================================

async def ask_for_classification(chat):
    cats = get_all_categories()
    menu = "".join(f"{cat[0]} - {cat[1]}\n" for cat in cats)
    msg = (
        f"**הגדרת ערוץ חדש:**\n"
        f"שם: {chat.title}\n"
        f"ID: `{chat.id}`\n\n"
        f"לאן לשייך? השב במספר:\n{menu}0 - התעלם"
    )
    await client.send_message(config.ADMIN_USER, msg)


@client.on(events.NewMessage(chats=config.ADMIN_USER))
async def handle_admin_reply(event):
    if not event.is_reply:
        return
    reply_msg = await event.get_reply_message()
    if "הגדרת ערוץ" not in reply_msg.text:
        return
    try:
        channel_id = int(reply_msg.text.split('ID: `')[1].split('`')[0])
    except Exception:
        await event.reply("❌ שגיאה בזיהוי ה-ID.")
        return
    text = event.text.strip()
    if text == '0':
        save_mapping(channel_id, -1, "Ignored")
        await event.reply("🚫 הערוץ יסונן.")
        return
    try:
        cat_data = get_category_by_id(int(text))
        if cat_data:
            target_id, name = cat_data
            save_mapping(channel_id, target_id, "Mapped Channel")
            await event.reply(f"✅ שויך לקטגוריה: **{name}**")
        else:
            await event.reply("❓ מספר קטגוריה לא קיים.")
    except ValueError:
        await event.reply("🔢 נא לשלוח מספר בלבד.")


@client.on(events.NewMessage(pattern='/new_cat', chats=config.ADMIN_USER))
async def create_category_command(event):
    try:
        args = event.text.split()
        if len(args) < 2:
            await event.reply("❌ שימוש: `/new_cat שם_הקטגוריה`")
            return
        cat_name = args[1]
        await event.reply(f"🔨 יוצר ערוץ לקטגוריה '{cat_name}'...")
        created = await client(CreateChannelRequest(
            title=f"Feed: {cat_name}",
            about=f"Auto-generated feed for {cat_name}",
            megagroup=True
        ))
        new_channel_id = created.chats[0].id
        new_channel_entity = created.chats[0]
        new_db_id = add_new_category(cat_name, new_channel_id)
        invite = await client(ExportChatInviteRequest(new_channel_entity))
        await event.reply(
            f"✅ **קטגוריה נוצרה!**\n"
            f"שם: {cat_name} | מספר: {new_db_id}\n"
            f"לינק: {invite.link}"
        )
    except Exception as e:
        await event.reply(f"⚠️ שגיאה: {str(e)}")


@client.on(events.NewMessage(pattern='/scan', chats=config.ADMIN_USER))
async def manual_scan(event):
    await event.reply("🔍 מתחיל סריקה...")
    async for dialog in client.iter_dialogs():
        if dialog.is_channel and not get_target_channel(dialog.id):
            await ask_for_classification(dialog.entity)
            await asyncio.sleep(1)
    await event.reply("🏁 הסריקה הסתיימה!")


@client.on(events.NewMessage(pattern='/status', chats=config.ADMIN_USER))
async def status_command(event):
    """Show dedup system status."""
    buf_size = len(_buffer)
    alert_status = "FLOOD ACTIVE" if _alert.is_active else "normal"
    with sqlite3.connect(config.DB_FILE) as conn:
        c = conn.cursor()
        cutoff = time.time() - (config.WINDOW_HOURS * 3600)
        c.execute("SELECT COUNT(*) FROM messages WHERE timestamp > ?", (cutoff,))
        db_count = c.fetchone()[0]
    await event.reply(
        f"**Bot Status:**\n"
        f"Buffer: {buf_size} msgs\n"
        f"DB window: {db_count} msgs\n"
        f"Alert: {alert_status}\n"
        f"Simhash threshold: {config.SIMHASH_THRESHOLD}"
    )


# =========================================================
# MAIN MESSAGE HANDLER
# =========================================================

@client.on(events.NewMessage)
async def main_handler(event):
    if event.is_private:
        return

    # Handle media groups (albums) separately
    if event.grouped_id:
        await handle_album_event(event)
        return

    source_id = event.chat_id
    text = event.message.message or ""

    # --- BAD_WORDS (fast, no DB) ---
    text_lower = text.lower()
    for word in config.BAD_WORDS:
        if word.lower() in text_lower:
            logger.info(f"[BAD_WORD] '{word}'")
            return

    # --- Target lookup ---
    target_id = get_target_channel(source_id)
    if not target_id or target_id == -1:
        return

    # --- Full dedup pipeline ---
    result = run_dedup(text, event.message, source_id)
    if result['action'] != 'PASS':
        logger.info(f"[BLOCK] {result['action']} — {result['reason']}")
        return

    text = result['cleaned_text']

    # --- pHash: בדיקת כפילות ויזואלית ---
    if await check_and_store_phash(event.message):
        logger.info("[BLOCK] photo_phash_dup")
        return

    # --- Store to DB ---
    save_message_record(text, event.message, source_id)

    # --- Format & send ---
    original_text = text
    translation = translate_to_hebrew(original_text)
    final_message = original_text
    if translation:
        final_message += f"\n\n--- תרגום ---\n{translation}"

    source_title = event.chat.title or str(source_id)
    source_link = (f"https://t.me/{event.chat.username}"
                   if event.chat.username else None)
    footer = (f"\n\nמקור: [{source_title}]({source_link})"
              if source_link else f"\n\nמקור: **{source_title}**")
    final_message += footer

    media = event.message.media
    if isinstance(media, MessageMediaWebPage):
        media = None

    try:
        await client.send_message(target_id, message=final_message,
                                  file=media, link_preview=False)
        logger.info(f"[SENT] → {target_id}")
    except Exception as e:
        logger.warning(f"[SEND ERROR] {e}")

    # --- בדיקת מילות מפתח → התראה לערוץ חדשות מרוכזות ---
    await send_security_alert(text, source_title, source_link)


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == '__main__':
    acquire_single_instance_lock()

    def _handle_shutdown(signum, frame):
        logger.info("🛑 Received signal %s — shutting down...", signum)
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_shutdown)
        except (ValueError, OSError):
            pass

    logger.info("Bot starting (zero-AI mode)...")
    init_db()

    MAX_RETRIES = 0       # 0 = infinite
    RETRY_DELAY = 10      # seconds between retries (grows exponentially)
    MAX_DELAY = 300       # cap at 5 minutes

    attempt = 0
    while True:
        attempt += 1
        delay = min(RETRY_DELAY * (2 ** min(attempt - 1, 5)), MAX_DELAY)
        try:
            logger.info(f"Connection attempt #{attempt}...")
            client.start()
            if not client.is_connected():
                logger.error("Client failed to connect!")
                raise ConnectionError("client.start() succeeded but is_connected() is False")
            logger.info("Client connected.")
            client.loop.run_until_complete(
                client.send_message(config.ADMIN_USER,
                                    f"✅ המערכת עלתה (zero-AI mode, aggressive dedup)"
                                    + (f" | reconnect #{attempt}" if attempt > 1 else ""))
            )
            attempt = 0  # reset on successful connection
            client.run_until_disconnected()
            # run_until_disconnected returned → connection dropped
            logger.warning("Disconnected from Telegram. Will reconnect...")
        except KeyboardInterrupt:
            logger.info("Shutting down (Ctrl+C)...")
            break
        except Exception as e:
            logger.error(f"Connection error (attempt #{attempt}): {e}")

        if MAX_RETRIES and attempt >= MAX_RETRIES:
            logger.critical(f"Gave up after {attempt} attempts.")
            break

        logger.info(f"Retrying in {delay}s...")
        time.sleep(delay)

    # Cleanup
    try:
        if client.is_connected():
            client.loop.run_until_complete(client.disconnect())
    except Exception:
        pass
    logger.info("Bot stopped.")
