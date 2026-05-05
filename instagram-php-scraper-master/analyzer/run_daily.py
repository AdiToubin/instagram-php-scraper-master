import os
from datetime import datetime, timezone
import sys

# Force UTF-8 encoding for Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
import re, json, time, base64, mimetypes, glob
from io import BytesIO
import requests
from requests.exceptions import Timeout, RequestException
from dotenv import load_dotenv
import logging
from collections import defaultdict
from collections import defaultdict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tqdm import tqdm  # Progress bar

# --- SIDECAR IMPORTS ---
try:
    from supabase import create_client, Client
    from ai_sidecar.vector_sidecar import analyze_story_sidecar
    from ai_sidecar.memory_store import save_to_memory
    SIDECAR_ENABLED = True
except ImportError as e:
    print(f"⚠️ Sidecar modules or supabase package not found. Sidecar disabled. Error: {e}")
    SIDECAR_ENABLED = False
# -----------------------

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "YOUR_OPENAI_API_KEY"
if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY")

RAW_TABLE = "story_raw"
REL_TABLE = "relevant_story"
REC_TABLE = "story_recommendations"
MODEL = "gpt-4o-mini"
MAX_ROWS = 1200

SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
SB_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
    "Accept": "application/json",
}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OA_HEADERS = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
}

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB – תשאירי מרווח סביר

# ============== LOGGING SETUP (IMPROVEMENT #3) ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs', 'run_daily.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============== DRY RUN SUPPORT (IMPROVEMENT #6) ==============
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ============== STATISTICS TRACKING (IMPROVEMENT #4) ==============
class RunStatistics:
    def __init__(self):
        self.start_time = time.time()
        self.total_stories = 0
        self.relevant_found = 0
        self.coupons_found = 0
        self.collab_ads_found = 0
        self.recommendations_found = 0
        self.errors = 0
        self.total_cost = 0.0
        self.processing_times = []
        
        # Per-influencer stats
        self.influencer_stats = defaultdict(lambda: {
            'total': 0,
            'coupons': 0,
            'collab_ads': 0,
            'recommendations': 0,
            'organic': 0
        })
    
    def add_story(self, username, content_type, has_coupon=False):
        """Track a processed story"""
        self.total_stories += 1
        self.influencer_stats[username]['total'] += 1
        
        if content_type == 'coupon':
            self.coupons_found += 1
            self.influencer_stats[username]['coupons'] += 1
            if has_coupon:
                self.relevant_found += 1
        elif content_type == 'collab_ad':
            self.collab_ads_found += 1
            self.influencer_stats[username]['collab_ads'] += 1
            self.relevant_found += 1
        elif content_type == 'recommendation':
            self.recommendations_found += 1
            self.influencer_stats[username]['recommendations'] += 1
        else:  # organic
            self.influencer_stats[username]['organic'] += 1
    
    def add_processing_time(self, seconds):
        self.processing_times.append(seconds)
    
    def add_cost(self, cost):
        self.total_cost += cost
    
    def add_error(self):
        self.errors += 1
    
    def print_summary(self):
        """Print run summary with influencer statistics"""
        elapsed = time.time() - self.start_time
        avg_time = sum(self.processing_times) / len(self.processing_times) if self.processing_times else 0
        
        print("\n" + "="*70)
        print("📊 RUN STATISTICS")
        print("="*70)
        print(f"⏱️  Total time: {elapsed:.1f}s")
        print(f"📝 Stories processed: {self.total_stories}")
        if self.total_stories > 0:
            print(f"✅ Relevant found: {self.relevant_found} ({self.relevant_found/self.total_stories*100:.1f}%)")
        else:
            print(f"✅ Relevant found: 0")
        print(f"   🎟️  Coupons: {self.coupons_found}")
        print(f"   🔗 Collab Ads: {self.collab_ads_found}")
        print(f"   💡 Recommendations: {self.recommendations_found}")
        print(f"❌ Errors: {self.errors}")
        print(f"💰 Total cost: ${self.total_cost:.4f}")
        print(f"⚡ Avg time/story: {avg_time:.2f}s")
        print("="*70)
        
        # Influencer report
        print("\n" + "="*70)
        print("👥 INFLUENCER STATISTICS")
        print("="*70)
        print(f"{'Username':<20} {'Total':>6} {'Coupons':>8} {'Ads':>6} {'Recs':>6} {'Organic':>8} {'Commercial %':>13}")
        print("-"*70)
        
        # Sort by commercial percentage
        sorted_influencers = sorted(
            self.influencer_stats.items(),
            key=lambda x: (x[1]['coupons'] + x[1]['collab_ads'] + x[1]['recommendations']) / max(x[1]['total'], 1),
            reverse=True
        )
        
        for username, stats in sorted_influencers:
            total = stats['total']
            if total == 0:
                continue
            
            commercial = stats['coupons'] + stats['collab_ads'] + stats['recommendations']
            commercial_pct = (commercial / total * 100)
            
            print(f"{username:<20} "
                  f"{total:>6} "
                  f"{stats['coupons']:>8} "
                  f"{stats['collab_ads']:>6} "
                  f"{stats['recommendations']:>6} "
                  f"{stats['organic']:>8} "
                  f"{commercial_pct:>12.1f}%")
        
        print("="*70)


def _normalize_brand_from_host(host: Optional[str]) -> Optional[str]:
    if not host:
        return None
    h = host.lower()
    h = h.replace("www.", "")
    parts = re.split(r"[.\-]+", h)
    # הוציאי TLD/דומיינים ידועים
    bad = {
        "com",
        "co",
        "il",
        "net",
        "org",
        "instagram",
        "cdninstagram",
        "fbcdn",
        "fna",
        "scontent",
    }
    cand = [p for p in parts if p and p not in bad]
    return cand[0] if cand else None


def _safe_brand(row: Dict[str, Any], result: Dict[str, Any]) -> str:
    # 1) אם המודל נתן brand — נשתמש בו
    b = (
        (result.get("brand") or "").strip()
        if isinstance(result.get("brand"), str)
        else None
    )
    if b:
        return b

    # 2) ננסה להפיק מכתובות חיצוניות (urls / result.urls)
    urls = (result.get("urls") or []) + (row.get("urls") or [])
    for u in urls:
        try:
            host = urlparse(u).hostname
        except Exception:
            host = None
        b2 = _normalize_brand_from_host(host)
        if b2:
            return b2

    # 3) אפשר לנסות גם מטוקנים שהמודל מחזיר (אם שינית את ה-prompt להחזיר brand_tokens)
    for t in result.get("brand_tokens") or []:
        t = str(t).strip().lower()
        if t:
            return t

    # 4) ברירת מחדל שמכבדת ה-NOT NULL
    return "unknown"


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    story_raw שומר את כל השדות ברמה עליונה — אין payload לפרוק.
    """
    return row


DEBUG = True  # אפשר גם לקרוא מסביבה: DEBUG = os.getenv("DEBUG", "0") == "1"


def _short(s, n=300):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s))
    return s if len(s) <= n else s[:n] + "…"


def dlog(*args, level="INFO", obj=None):
    if not DEBUG:
        return
    ts = datetime.now().strftime("%H:%M:%S")
    msg = " ".join(str(a) for a in args)
    print(f"[{ts}] [{level}] {msg}")
    if obj is not None:
        try:
            print(_short(json.dumps(obj, ensure_ascii=False), 1200))
        except Exception:
            print(_short(str(obj), 1200))


# ============== CONFIG ==============


# ====================================

LAST_CALL_TS = 0.0
MIN_INTERVAL_S = 0.5


def _safe_name(row: Dict[str, Any], result: Dict[str, Any]) -> str:
    """
    מחזירה תמיד name לא-ריק (username של המשפיענית).
    story_raw מכיל username ישירות — אין payload.
    """
    candidates = [
        result.get("name"),
        row.get("username"),
        row.get("name"),
    ]
    for c in candidates:
        if c and str(c).strip() and not str(c).strip().isdigit():
            return str(c).strip()
    user_id = row.get("user_id")
    if user_id:
        return f"user_{user_id}"
    return "unknown"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sb_get(path: str, params: Dict[str, str]) -> requests.Response:
    return requests.get(path, headers=SB_HEADERS, params=params, timeout=30)


def sb_patch(
    path: str, params: Dict[str, str], body: Dict[str, Any]
) -> requests.Response:
    return requests.patch(
        path, headers=SB_HEADERS, params=params, data=json.dumps(body), timeout=30
    )


def sb_post(
    path: str,
    body: Dict[str, Any],
    prefer: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
) -> requests.Response:
    headers = SB_HEADERS.copy()
    if prefer:
        headers["Prefer"] = prefer
    return requests.post(
        path, headers=headers, params=params, data=json.dumps(body), timeout=30
    )


def get_raw_rows(limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    # Filter: processing column is NULL (not yet touched)
    # If your DB defaults 'processing' to {}, you might need a different filter like processing->status=is.null
    params = {
        "select": "*",
        "order": "inserted_at.desc",
        "limit": str(limit),
        "processing": "is.null", 
    }
    r = sb_get(url, params)
    if r.status_code >= 400:
        print("❌ Supabase error (raw fetch):", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


def upsert_recommendation(row: Dict[str, Any], result: Dict[str, Any]):
    """
    Saves 'recommendation' stories to a separate table.
    """
    payload = {
        "media_id": row.get("media_id"),
        "user_id": row.get("user_id"),
        "name": _safe_name(row, result),
        "brand": _safe_brand(row, result),
        "date": row.get("taken_at_iso"),
        "Description": result.get("Description"),
        "category": result.get("category") or "other",
        # Optional fields that might be empty for recommendations
        "url": result.get("url"),
    }

    url = f"{SUPABASE_REST}/{REC_TABLE}"
    r = sb_post(
        url,
        payload,
        prefer="return=representation,resolution=merge-duplicates",
        params={"on_conflict": "media_id"},
    )
    if r.status_code >= 400:
        print(f"❌ upsert recommendation failed: {r.status_code} {r.text}")
    r.raise_for_status()
    return r.json()


def set_processing_status(
    media_id: str,
    status: str,
    last_error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
):
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {"media_id": f"eq.{media_id}"}
    payload = {
        "processing": {
            "status": status,
            "last_error": last_error,
            "ts": now_iso(),
            **(extra or {}),
        }
    }
    r = sb_patch(url, params, payload)
    if r.status_code >= 400:
        print("⚠️ set_processing_status failed:", r.status_code, r.text)


BRAND_MAP_HE_IL = {
    "קולנטה": "kolenta",
    "מילא": "mila",
    "נעמה": "naama",
    "פלטין אקספרס": "platinexpress",
    "הום סטייל": "homestyle",
    "חני וינברגר": "chanivainberger",
    "מהדרין אונליין": "mehadrinonline",
    "ריזרבד": "reserved",
}
COUPON_RE = re.compile(r"\b(?![0-9]{3,})([A-Za-z0-9][A-Za-z0-9_-]{3,19})\b")

IGNORE_COUPONS = {
    "INSTAGRAM", "WHATSAPP", "FACEBOOK", "PHONE", "SEND", "TEXT", "FBCLID", "IGSH", 
    "LINK", "MESSAGE", "DM", "DIRECT", "POST", "STORY", "REEL", "TIKTOK", "YOUTUBE",
    "TWITTER", "SNAPCHAT", "TELEGRAM", "LINKEDIN", "PINTEREST", "TUMBLR", "REDDIT",
    "GOOGLE", "APPLE", "ANDROID", "IOS", "APP", "STORE", "PLAY", "SHOP", "BUY",
    "SALE", "OFF", "CODE", "COUPON", "DISCOUNT", "PROMO", "FREE", "GIFT", "WIN",
    "GIVEAWAY", "CONTEST", "ENTER", "JOIN", "FOLLOW", "LIKE", "SHARE", "COMMENT",
    "TAG", "MENTION", "SUBSCRIBE", "SIGNUP", "REGISTER", "LOGIN", "LOGOUT", "ACCOUNT",
    "PROFILE", "PAGE", "GROUP", "CHANNEL", "CHAT", "CALL", "EMAIL", "WEBSITE", "URL",
    "HTTP", "HTTPS", "WWW", "COM", "CO", "IL", "NET", "ORG", "GOV", "EDU", "INFO",
    "BIZ", "ME", "TV", "IO", "AI", "LY", "GL", "BE", "IT", "US", "UK", "CA", "AU",
    "DE", "FR", "ES", "RU", "JP", "CN", "IN", "BR", "MX", "ID", "TR", "SA", "AE",
    "QA", "KW", "OM", "BH", "JO", "LB", "EG", "MA", "DZ", "TN", "LY", "SD", "YE",
    "SY",
    # Brand names that look like codes
    "ADDICT", "DRESS", "NOIR", "CAFENOIR", "BAUM", "ORIN", "ORINDRESS",
    "TINC", "TINCE", "HANA", "CAFE"
}

# ============== HELPER FUNCTIONS (IMPROVEMENTS) ==============

def calculate_openai_cost(model, input_tokens=500, output_tokens=200, has_image=False):
    """Calculate OpenAI API cost"""
    if model.startswith("gpt-4o"):
        if has_image:
            return 0.00765  # Vision pricing
        else:
            # Text-only pricing approximation
            input_cost = (input_tokens / 1_000_000) * 5
            output_cost = (output_tokens / 1_000_000) * 15
            return input_cost + output_cost
    elif "mini" in model:
        input_cost = (input_tokens / 1_000_000) * 0.150
        output_cost = (output_tokens / 1_000_000) * 0.600
        return input_cost + output_cost
    return 0.0

def is_valid_coupon_code(code: str, username: str = "") -> bool:
    """Validate if a string is a valid coupon code"""
    if not code or len(code) < 4 or len(code) > 20:
        return False
    
    code_upper = code.upper()
    
    # Check against blocklist
    if code_upper in IGNORE_COUPONS:
        return False
    
    # Block time patterns
    if re.match(r'^\d{2}-\d{2}$', code_upper):
        return False
    
    # Block OCR garbage
    if re.match(r'^[0-9]+[A-Z]+[0-9]+[A-Z]+', code_upper):
        return False
    
    # Must have at least 2 letters
    letter_count = sum(1 for c in code_upper if c.isalpha())
    if letter_count < 2:
        return False

    # Block if it's all digits with dashes
    if code_upper.replace("-", "").isdigit():
        return False

    return True

def validate_ai_result(result, row):
    """Validate and clean AI response"""
    if not result:
        return result
    
    username = row.get('username', '')
    
    # Validate main coupon
    if result.get('coupon'):
        code = result['coupon'].upper()
        if not is_valid_coupon_code(code, username):
            logger.warning(f"AI returned invalid coupon '{code}', removing")
            result['coupon'] = None
    
    # Validate coupons array
    if result.get('coupons'):
        valid_coupons = [c.upper() for c in result['coupons'] if is_valid_coupon_code(c, username)]
        result['coupons'] = valid_coupons
    
    # Ensure category is never empty
    if not result.get('category') or result.get('category') in ('', 'null', 'none'):
        result['category'] = 'other'
    
    # Ensure brand is never "General" or empty
    if result.get('brand') in ('General', 'Unknown', 'Brand', '', None):
        result['brand'] = 'unknown'

    return result

def extract_and_validate_url_codes(urls: List[str], username: str = "") -> List[Dict[str, str]]:
    """
    Extract potential coupon codes from URL paths and validate them.
    Returns list of validated codes with metadata.
    """
    codes = []
    username_lower = username.lower()
    
    for url in urls:
        try:
            parsed = urlparse(url)
            path = parsed.path
            
            # Split path by common separators
            path_parts = re.split(r'[/_\-]', path)
            
            for part in path_parts:
                if not part or len(part) < 4:
                    continue
                
                part_upper = part.upper()
                
                # Apply validation rules
                if not is_valid_coupon_code(part_upper, username_lower):
                    continue
                
                # Check if it matches username (likely influencer code)
                is_influencer_code = username_lower and (
                    part.lower() in username_lower or 
                    username_lower in part.lower()
                )
                
                codes.append({
                    'code': part_upper,
                    'source': 'url_path',
                    'url': url,
                    'is_influencer_code': is_influencer_code,
                    'snippet': f"URL: {url[:100]}"
                })
        
        except Exception as e:
            logger.warning(f"Failed to extract code from URL {url}: {e}")
            continue
    
    # Deduplicate
    seen = set()
    unique_codes = []
    for code_info in codes:
        code = code_info['code']
        if code not in seen:
            seen.add(code)
            unique_codes.append(code_info)
    
    return unique_codes

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((Timeout, RequestException)),
    reraise=True
)
def call_openai_with_retry(payload: Dict[str, Any]) -> requests.Response:
    """Call OpenAI API with automatic retry"""
    logger.debug("Calling OpenAI API...")
    response = requests.post(
        OPENAI_URL,
        headers=OA_HEADERS,
        json=payload,
        timeout=90
    )
    if response.status_code >= 500:
        logger.warning(f"OpenAI server error {response.status_code}, will retry...")
        response.raise_for_status()
    if response.status_code == 429:
        logger.warning("Rate limited, will retry...")
        response.raise_for_status()
    return response


def extract_pairs_from_stickers(row) -> list[dict]:
    out, texts = [], []
    username = (row.get("username") or "").lower()
    
    for s in row.get("stickers") or []:
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            texts.extend(re.split(r"[\r\n]+", t))
            
    for line in texts:
        line = line.strip()
        if not line:
            continue
            
        # Ignore lines that are just URLs to profiles/posts
        if "instagram.com" in line.lower():
             continue

        parts = re.split(r"\s*[-–:]\s*", line, maxsplit=1)
        # If no split, assume whole line is "right" side
        if len(parts) == 2:
             brand_he, right = parts
             brand_norm = next(
                (v for k, v in BRAND_MAP_HE_IL.items() if k in brand_he), "unknown"
            )
        else:
             right = line
             brand_norm = "unknown"

        for m in COUPON_RE.finditer(right):
            code = m.group(1).upper()
            if len(code) < 4:
                continue
            if code in IGNORE_COUPONS:
                continue
            if code.replace("-", "").isdigit() and len(code) > 6: # Phone numbers
                continue
            
            # ===== NEW STRICT VALIDATION =====
            
            # 1. Block time patterns (00-22, 30-15, etc.)
            if re.match(r'^\d{2}-\d{2}$', code):
                continue  # This is a time, not a coupon!
            
            # 2. Block patterns that look like OCR garbage
            # Real coupons have clear structure: WORD10, SAVE20, etc.
            # OCR garbage looks like: 2ARA3OP, A3OH (random mix)
            if re.match(r'^[0-9]+[A-Z]+[0-9]+[A-Z]+', code):
                continue  # Too chaotic, likely OCR error
            
            # 3. Must have at least ONE letter (not just numbers and dashes)
            if not re.search(r'[A-Z]', code):
                continue
            
            # 4. Block codes that are mostly numbers (e.g., "2024", "1234")
            letter_count = sum(1 for c in code if c.isalpha())
            if letter_count < 2:  # Need at least 2 letters
                continue
            
            # Username check: if code matches start of username
            if username and (code.lower() in username or username in code.lower()):
                continue
                
            out.append(
                {
                    "brand": brand_norm,
                    "code": code,
                    "source": "sticker",
                    "snippet": line[:140],
                }
            )
    return out


def load_all_processed_ids(user_id: Optional[str] = None) -> set:
    """
    טוענת את כל ה-media_id שכבר עברו עיבוד — לפי שדה processing ב-story_raw.
    """
    processed_ids = set()
    try:
        url = f"{SUPABASE_REST}/{RAW_TABLE}"
        params = {"select": "media_id", "processing": "not.is.null"}
        if user_id:
            params["user_id"] = f"eq.{user_id}"
        r = sb_get(url, params)
        if r.status_code == 200:
            for row in r.json():
                if row.get("media_id"):
                    processed_ids.add(row["media_id"])
        else:
            logger.warning(f"Failed to load processed IDs: {r.status_code}")
    except Exception as e:
        logger.error(f"Error loading processed IDs: {e}")
    return processed_ids


def already_in_relevant(media_id: str) -> bool:
    """
    בודקת אם הסטורי כבר עובד — לפי שדה processing ב-story_raw.
    """
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {"select": "processing", "media_id": f"eq.{media_id}", "limit": "1"}
    r = sb_get(url, params)
    if r.status_code == 200 and r.json():
        processing = r.json()[0].get("processing")
        if isinstance(processing, dict) and processing.get("status") in ("ok", "done", "non_relevant", "skipped"):
            return True
    return False


# ============== CONTENT DEDUPLICATION (IMPROVEMENT #7) ==============
import hashlib
from datetime import datetime, timedelta

def generate_coupon_hash(result: Dict[str, Any]) -> str:
    """
    ✅ Generate unique hash from commercial content.
    Same coupon/brand/link = same hash → prevents duplicates!
    """
    components = [
        result.get('code', '').upper(),
        result.get('brand', '').lower(),
        result.get('url', ''),
        result.get('verdict', '')
    ]
    
    content = '|'.join(str(c) for c in components if c)
    return hashlib.md5(content.encode()).hexdigest()[:16]


def is_duplicate_in_timewindow(
    user_id: str,
    content_hash: str,
    hours: int = 48
) -> bool:
    """
    בודקת ב-story_raw אם אותו content_hash כבר עובד ב-48 שעות האחרונות.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        url = f"{SUPABASE_REST}/{RAW_TABLE}"
        params = {
            "select": "media_id",
            "user_id": f"eq.{user_id}",
            "content_hash": f"eq.{content_hash}",
            "inserted_at": f"gte.{cutoff}",
            "processing": "not.is.null",
            "limit": "1",
        }
        r = sb_get(url, params)
        if r.status_code == 200 and r.json():
            logger.info(f"⏭️ Duplicate found: hash={content_hash}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Dedup check failed: {e}")
        return False
# ====================================================================


# ============== ACTIVE LEARNING (IMPROVEMENT #8) ==============
def save_correction(
    media_id: str,
    user_id: str,
    ai_result: Dict[str, Any],
    correct_verdict: str,
    correct_code: Optional[str] = None,
    correct_brand: Optional[str] = None,
    story_context: Optional[str] = None,
    note: Optional[str] = None
) -> Optional[requests.Response]:
    """
    ✅ Save user correction for Active Learning.
    The system will learn from this correction in future analyses!
    """
    payload = {
        "media_id": media_id,
        "user_id": user_id,
        "ai_verdict": ai_result.get("verdict"),
        "ai_code": ai_result.get("code"),
        "ai_brand": ai_result.get("brand"),
        "ai_confidence": ai_result.get("confidence", 0.0),
        "correct_verdict": correct_verdict,
        "correct_code": correct_code,
        "correct_brand": correct_brand,
        "story_context": story_context,
        "correction_note": note
    }
    
    try:
        url = f"{SUPABASE_REST}/user_corrections"
        r = sb_post(
            url,
            payload,
            prefer="return=representation,resolution=merge-duplicates",
            params={"on_conflict": "media_id"}
        )
        
        if r.status_code >= 400:
            logger.error(f"Failed to save correction: {r.status_code} {r.text}")
            return None
        else:
            logger.info(f"✅ Correction saved for {media_id}: {correct_verdict}")
            return r
    except Exception as e:
        logger.error(f"Error saving correction: {e}")
        return None


def load_user_corrections(user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    ✅ Load recent corrections for this user.
    These will be used as high-priority examples in Few-Shot learning.
    """
    try:
        url = f"{SUPABASE_REST}/user_corrections"
        params = {
            "select": "*",
            "user_id": f"eq.{user_id}",
            "order": "corrected_at.desc",
            "limit": str(limit)
        }
        
        r = sb_get(url, params)
        if r.status_code == 200:
            corrections = r.json()
            logger.info(f"📚 Loaded {len(corrections)} corrections for user {user_id}")
            return corrections
        else:
            logger.warning(f"Failed to load corrections: {r.status_code}")
            return []
    except Exception as e:
        logger.error(f"Error loading corrections: {e}")
        return []
# ====================================================================


def upsert_relevant(row: Dict[str, Any], result: Dict[str, Any]):
    # ✅ DEDUPLICATION: Check if this coupon was already saved recently
    content_hash = generate_coupon_hash(result)
    user_id = row.get("user_id")
    
    if is_duplicate_in_timewindow(user_id, content_hash, hours=48):
        logger.info(f"⏭️ Skipping duplicate coupon: {result.get('code')} from {result.get('brand')}")
        return None  # Don't insert duplicate
    
    # דדופ רשימת הפריטים (למקרה של כפילויות)
    items = result.get("coupon_items") or []
    dedup = []
    seen = set()
    for it in items:
        brand = (it.get("brand") or "unknown").strip().lower()
        code = (it.get("code") or "").strip().upper()
        if not code:
            continue
        key = (brand, code)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(
            {
                "brand": brand,
                "code": code,
                "source": (it.get("source") or "ocr").lower(),
                "snippet": it.get("snippet"),
            }
        )

    payload = {
        "name": _safe_name(row, result),
        "user_id": user_id,
        "brand": _safe_brand(row, result),
        "coupon": result.get("coupon"),
        "url": result.get("url"),
        "date": row.get("taken_at_iso"),
        "description": (
            (result.get("Description") or "").strip()
            or (row.get("caption_text") or "").strip()
            or (row.get("ocr_text") or "").strip()
            or None
        ),
    }

    url = f"{SUPABASE_REST}/{REL_TABLE}"
    r = sb_post(
        url,
        payload,
        prefer="return=representation,resolution=merge-duplicates",
        params={"on_conflict": "name,user_id"},
    )
    if r.status_code >= 400:
        print("❌ upsert relevant failed:", r.status_code, r.text)
    r.raise_for_status()

#############################################################
# NOTE: SYSTEM_PROMPT removed - now using ai_sidecar/vector_sidecar.py
#############################################################






JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


URL_RE = re.compile(r"https?://[^\s)\]]+", re.IGNORECASE)

BAD_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "cdninstagram.com",
    "fbcdn.net",
    "fna.fbcdn.net",
    "scontent.cdninstagram.com",
}


def extract_all_urls(row: Dict[str, Any]) -> List[str]:
    out = []

    # 1) urls[]
    for u in row.get("urls") or []:
        if isinstance(u, dict) and u.get("text"):
            out.append(u["text"])
        elif isinstance(u, str):
            out.append(u)

    # 2) stickers[].text
    for s in row.get("stickers") or []:
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            out += URL_RE.findall(t)

    # 3) caption/ocr/raw_text_candidates
    for fld in ("caption_text", "ocr_text"):
        v = row.get(fld)
        if v:
            out += URL_RE.findall(v)
    for t in row.get("raw_text_candidates") or []:
        out += URL_RE.findall(str(t))

    # ייחוד וניקוי
    seen = set()
    clean = []
    for u in out:
        u = u.strip().strip(".,);]")
        if u and u not in seen:
            seen.add(u)
            clean.append(u)
    return clean


def host_of(u: str) -> Optional[str]:
    try:
        return urlparse(u).hostname
    except Exception:
        return None


def is_brand_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.lower()
    if h in BAD_HOSTS:
        return False
    # תזרקי גם תתי־דומיינים של האזורים האסורים
    for bad in BAD_HOSTS:
        if h.endswith("." + bad):
            return False
    return True


def has_marketing_words(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    heb_terms = [
        "קופון",
        "קוד",
        "הנחה",
        "מבצע",
        "סייל",
        "לינק",
        "קישור",
        "קנייה",
        "לרכישה",
        "קנו",
        "חדש",
        "הושק",
        "השקה",
        "חזר",
        "חזרה למלאי",
        "חזר למלאי",
    ]
    eng_terms = [
        "coupon",
        "code",
        "promo",
        "discount",
        "sale",
        "shop",
        "buy",
        "link",
        "new",
        "launch",
        "launched",
        "is back",
        "back in stock",
    ]
    perc_or_price = any(sym in t for sym in ["%", "₪", "$", "€"])
    return perc_or_price or any(x in t for x in heb_terms + eng_terms)


def infer_category_from_text_or_image(row, result=None):
    text_fields = " ".join(
        [
            str(row.get("caption_text") or ""),
            str(row.get("ocr_text") or ""),
            str(row.get("description") or ""),
            str(row.get("brand") or ""),
            " ".join(row.get("hashtags") or []),
        ]
    ).lower()

    mapping = {
        "fashion": [
            "בגד",
            "בגדים",
            "ביגוד",
            "חולצה",
            "שמלה",
            "מכנס",
            "ג'ינס",
            "טרנינג",
            "פיגמה",
            "פיג'מה",
            "פיג’מה",
            "הלבשה",
            "תחתונה",
            "אופנה",
            "reserved",
            "crazyline",
        ],
        "beauty": ["שיער", "קרם", "שפתון", "טיפוח", "מסכה", "פרופילוס", "prohairplus"],
        "home": ["רהיט", "הום", "בית", "מטבח", "עיצוב", "מיטה", "כורסה"],
        "food": ["אוכל", "שוקולד", "קפה", "בישול", "מתכון", "מאפה"],
        "tech": ["אתר", "אפליקציה", "מחשב", "טלפון", "גאדג", "טכנולוג"],
        "kids": ["תינוק", "ילד", "צעצוע", "משחק", "עגלה", "מוצץ", "טיטול"],
        "travel": ["מלון", "חופשה", "טיסה", "נופש", "צימר", "חו''ל", "יעד"],
    }

    for cat, kws in mapping.items():
        if any(k in text_fields for k in kws):
            return cat
    return "other"


# call_openai_filter_old() removed - use call_openai_filter() instead






def dlog(*args, obj=None, level="INFO"):
    if not DEBUG:
        return
    ts = datetime.now().strftime("%H:%M:%S")
    head = f"[{ts}] [{level}]"
    if obj is not None:
        try:
            print(head, *args)
            print(json.dumps(obj, ensure_ascii=False))
            return
        except Exception:
            pass
    print(head, *args)


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    payload = r.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = None
    if isinstance(payload, dict):
        # העדיפי ערכים מה-payload אם חסרים ברמה העליונה
        for k in (
            "type",
            "urls",
            "user_id",
            "hashtags",
            "mentions",
            "ocr_text",
            "stickers",
            "username",
            "image_url",
            "permalink",
            "video_url",
            "media_meta",
            "frames_used",
            "caption_text",
            "content_hash",
            "source_flags",
            "taken_at_iso",
            "language_guess",
            "ocr_confidence",
            "expiring_at_iso",
            "brand_candidates",
            "raw_text_candidates",
        ):
            if r.get(k) is None and payload.get(k) is not None:
                r[k] = payload.get(k)
    return r


BAD_IMAGE_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "cdninstagram.com",
    "scontent.cdninstagram.com",
    "fbcdn.net",
    "fna.fbcdn.net",
}


def _short(s, n=1000):
    try:
        s = str(s)
        s = re.sub(r"\s+", " ", s)
        return s[:n]
    except Exception:
        return s



# -------------------------------------------------------------------
# HELPER FUNCTIONS & CONSTANTS (Global Scope)
# -------------------------------------------------------------------

BAD_IMAGE_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "cdninstagram.com",
    "scontent.cdninstagram.com",
    "fbcdn.net",
    "fna.fbcdn.net",
}

BAD_URL_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "cdninstagram.com",
    "fbcdn.net",
    "fna.fbcdn.net",
    "scontent.cdninstagram.com",
}

def is_bad_image_host(url: str) -> bool:
    try:
        h = urlparse(url).netloc.lower()
        if h in BAD_IMAGE_HOSTS:
            return True
        return any(h.endswith("." + bh) for bh in BAD_IMAGE_HOSTS)
    except Exception:
        return True

def host_of(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc
    except Exception:
        return None

def is_brand_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.lower()
    if h in BAD_URL_HOSTS:
        return False
    for bad in BAD_URL_HOSTS:
        if h.endswith("." + bad):
            return False
    return True

def brand_tokens_from_urls(urls: List[str]) -> List[str]:
    toks = []
    for u in urls:
        # 1. Host parts
        h = (host_of(u) or "").lower().replace("www.", "")
        parts = re.split(r"[\.\-]+", h)
        for p in parts:
            if len(p) >= 4 and p not in (
                "instagram", "cdninstagram", "fbcdn", "fna", "com", "co", "il", 
                "link", "bio", "shop", "store", "app", "api", "www"
            ):
                toks.append(p)
        
        # 2. Path parts (Critical for affiliate links like humanz.ai/BRAND/...)
        try:
            path = urlparse(u).path
            path_parts = re.split(r"[\/\-_]+", path)
            for p in path_parts:
                if p and len(p) >= 3 and not p.isdigit(): # Avoid numeric IDs
                     if p.lower() not in ("posts", "stories", "reel", "p", "tv", "explore"):
                        toks.append(p)
        except Exception:
            pass

    return sorted(set(toks))

def fetch_image_as_data_uri(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        content = resp.content
        if len(content) > MAX_IMAGE_BYTES:
            print(f"⚠️ Image too big ({len(content)} bytes), skipping: {url[:100]}")
            return None
        
        ctype = resp.headers.get("Content-Type", "")
        if not ctype:
            # guess
            ctype, _ = mimetypes.guess_type(url)
        if not ctype:
            ctype = "image/jpeg"

        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception as e:
        print(f"⚠️ Image fetch failed: {e}")
        return None


def call_openai_filter(row: Dict[str, Any], context_str: str = "") -> Optional[Dict[str, Any]]:
    """
    IMPROVED version with:
    1. URL code extraction
    2. Retry logic (tenacity)
    3. Better image handling
    4. Validation of hallucinations
    """
    
    # ---------- local logging (uses global dlog() if exists) ----------
    def _dlog(*args, level="INFO", obj=None):
        try:
            if "dlog" in globals() and callable(globals()["dlog"]):
                globals()["dlog"](*args, level=level, obj=obj)
                return
        except Exception:
            pass
        # fallback minimal logger
        ts = datetime.now().strftime("%H:%M:%S")
        msg = " ".join(str(a) for a in args)
        print(f"[{ts}] [{level}] {msg}")
        if obj is not None:
            try:
                print(_short(json.dumps(obj, ensure_ascii=False), 1200))
            except Exception:
                print(_short(str(obj), 1200))

    # ---------- small utils (self-contained) ----------
    def _short(s, n=420):
        if not s:
            return ""
        s = str(s).replace("\n", " ").strip()
        if len(s) > n:
            return s[:n] + "..."
        return s

    # ---------- normalize & introspect ----------
    media_id = row.get("media_id")
    _dlog("➡️ call_openai_filter for media_id:", media_id)

    # URLs & brand signals
    urls_all = extract_all_urls(row)
    _dlog("URLs collected:", obj=urls_all)
    
    # ✅ IMPROVEMENT 1: Extract codes from URLs
    url_codes = extract_and_validate_url_codes(urls_all, row.get("username", ""))
    url_extracted_codes = [c["code"] for c in url_codes]
    if url_extracted_codes:
        _dlog("🔍 Found codes in URLs:", obj=url_extracted_codes)

    brand_urls = [u for u in urls_all if is_brand_host(host_of(u))]
    _dlog("Brand URLs:", obj=brand_urls)

    brand_tokens = brand_tokens_from_urls(brand_urls)
    _dlog("Brand tokens:", obj=brand_tokens)

    # stickers summarized
    stickers_texts = []
    for s in row.get("stickers") or []:
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            stickers_texts.append(_short(t, 1400))

    hashtags = [
        ("#" + h) if not str(h).startswith("#") else str(h)
        for h in (row.get("hashtags") or [])
    ]

    # marketing intent from all visible text
    all_text = " ".join(
        [
            str(row.get("caption_text") or ""),
            str(row.get("ocr_text") or ""),
            " ".join(stickers_texts),
        ]
    )
    hint_marketing_intent = has_marketing_words(all_text)
    _dlog("Marketing intent?", hint_marketing_intent)

    # ---------- build user payload ----------
    user_blob = {
        "context_from_previous_stories": context_str,
        "username": row.get("username"),
        "caption_text": _short(row.get("caption_text")),
        "ocr_text": _short(row.get("ocr_text")),
        "stickers_texts": stickers_texts[:10],
        "hashtags": hashtags[:12],
        "urls": urls_all[:12],
        "brand_url_present": bool(brand_urls),
        "brand_urls": brand_urls[:8],
        "brand_tokens": brand_tokens[:8],
        # ✅ Inject URL codes hint
        "url_extracted_codes": url_extracted_codes,
        "hints": {
            "brand_url_present": bool(brand_urls),
            "marketing_intent": hint_marketing_intent,
        },
        "permalink_present": bool(row.get("permalink")),
        "has_image": bool(row.get("image_url")),
        "has_video": bool(row.get("video_url")),
        "image_url": row.get("image_url"),
    }
    _dlog("User blob (to model):", obj=user_blob)

    # ---------- image handling (CDN-safe) ----------
    img_ref = None
    if row.get("image_url"):
        if is_bad_image_host(row["image_url"]):
            _dlog("Image host blocked – converting to data URI…")
            img_ref = fetch_image_as_data_uri(row["image_url"])
            if not img_ref:
                _dlog(
                    "Failed to build data URI, proceeding without image", level="WARN"
                )
        else:
            img_ref = row["image_url"]

    # ---------- messages ----------
    content_parts = [
        {"type": "text", "text": json.dumps(user_blob, ensure_ascii=False)}
    ]
    if img_ref:
        content_parts.append(
            {"type": "image_url", "image_url": {"url": img_ref, "detail": "low"}}
        )

    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content_parts},
        ],
        "response_format": {"type": "json_object"},
    }
    _dlog("OpenAI payload (short): model=", MODEL)

    # ---------- send with retries (IMPROVEMENT #5) ----------
    try:
        resp = call_openai_with_retry(payload)
        
        # 400 invalid_image_url => try once to force data URI if not done
        if (
            resp.status_code == 400
            and img_ref
            and isinstance(img_ref, str)
            and not img_ref.startswith("data:")
        ):
            _dlog("400 invalid_image_url; forcing data URI fallback", level="WARN")
            data_uri = fetch_image_as_data_uri(img_ref)
            if data_uri:
                content_parts[-1]["image_url"]["url"] = data_uri
                payload["messages"][1]["content"] = content_parts
                # Retry with new payload
                resp = call_openai_with_retry(payload)

        body_text = resp.text
        _dlog("OpenAI raw response (short):", obj=_short(body_text, 1200))

        if resp.status_code == 200:
            try:
                content = resp.json()["choices"][0]["message"]["content"]
                _dlog("Model content (short):", obj=_short(content, 800))
                result = json.loads(content)
                
                # ✅ Validation Logic applied here or in main loop
                return result
                
            except Exception:
                # fallback naive JSON extraction
                try:
                    start = body_text.index("{")
                    end = body_text.rindex("}") + 1
                    result = json.loads(body_text[start:end])
                    return result
                except Exception as e:
                    _dlog(
                        "JSON parse failed (even fallback).", level="ERROR", obj=str(e)
                    )
                    return None

        _dlog(
            "❌ OpenAI error:",
            level="ERROR",
            obj={"status": resp.status_code, "body": _short(body_text, 800)},
        )
        return None

    except Exception as e:
        _dlog("❌ Fatal OpenAI error:", level="ERROR", obj=str(e))
        return None



def load_id_to_username_map():
    """
    Loads the latest user_ids_*.json file to create a mapping of user_id -> username.
    This ensures we use the correct influencer name even if the story data is missing it.
    """
    try:
        files = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'user_ids_*.json'))
        if not files:
            print("⚠️ No user_ids_*.json files found for username mapping.")
            return {}
        
        latest_file = max(files, key=os.path.getctime)
        print(f"📂 Loading username mapping from: {latest_file}")
        
        with open(latest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Map user_id -> username
            mapping = {}
            # Handle both list and dict with "results" key
            items = data.get("results", []) if isinstance(data, dict) else data
            
            if isinstance(items, list):
                for u in items:
                    if isinstance(u, dict):
                        uid = str(u.get("user_id") or "")
                        uname = u.get("username")
                        if uid and uname:
                            mapping[uid] = uname
            
            print(f"✅ Loaded {len(mapping)} username mappings.")
            return mapping
    except Exception as e:
        print(f"❌ Error loading username map: {e}")
        return {}


def main():
    # Initialize statistics (IMPROVEMENT #4)
    stats = RunStatistics()
    logger.info(f"🚀 Starting run_daily.py (Improvements enabled, DRY_RUN={DRY_RUN})")

    # Load username mapping first
    id_map = load_id_to_username_map()

    try:
        rows = get_raw_rows(MAX_ROWS)
    except Exception as e:
        logger.error(f"❌ Failed to fetch rows: {e}")
        return
    dlog(
        "SB auth header startswith 'eyJ'? (anon-ish):",
        obj=str(SB_HEADERS.get("Authorization", ""))[:10],
    )

    # 1. Group rows by user_id
    rows_by_user = {}
    for r in rows:
        uid = str(r.get("user_id") or "unknown")
        if uid not in rows_by_user:
            rows_by_user[uid] = []
        rows_by_user[uid].append(r)

    print(f"Fetched {len(rows)} rows, grouped into {len(rows_by_user)} users.")

    # 2. Process each user's stories chronologically
    for uid, user_rows in rows_by_user.items():
        # Sort by date (oldest first) so context builds up naturally
        user_rows.sort(key=lambda x: x.get("taken_at_iso") or "")
        
        # Get username for logging
        username = "unknown"
        if user_rows:
             # Try to resolve username from the first row using our map or row data
            _dummy_res = {} # helper to use _safe_name logic
            username = _safe_name(user_rows[0], _dummy_res)
            if uid in id_map:
                username = id_map[uid]

        print(f"\n👤 Processing User: {username} ({len(user_rows)} stories)")
        
        # Context buffer for this user's session
        user_context = []

        for idx, row in enumerate(user_rows, 1):
            media_id = row.get("media_id") or f"row_{idx}"
            print(f"  👉 Story {idx}/{len(user_rows)}: {media_id}")
            
            # Normalize
            row = normalize_row(row)
            
            # Ensure username is correct
            if uid in id_map:
                row["username"] = id_map[uid]
                if isinstance(row.get("payload"), dict):
                    row["payload"]["username"] = id_map[uid]

            # Check if already processed
            try:
                if already_in_relevant(media_id):
                    print("    ↩️ Skipped (already relevant)")
                    # Add to context anyway so subsequent stories know about it? 
                    # For now, we skip fully to save time, but ideally we'd load its result.
                    continue
            except Exception:
                pass

            # Prepare context string
            context_str = ""
            if user_context:
                context_str = "\n".join([f"- {c}" for c in user_context[-3:]]) # Last 3 stories

            # Call OpenAI with Context
            try:
                result = call_openai_filter(row, context_str=context_str)
                print(f"DEBUG: OpenAI returned type: {type(result)}")
                if result:
                    print(f"DEBUG: OpenAI is_relevant: {result.get('is_relevant')}")
            except Exception as e:
                print("    ❌ OpenAI failed:", e)
                set_processing_status(media_id, "error", str(e))
                continue
            
            # ... (Rest of the logic: Fallback, Upsert, etc.) ...
            
            # Update Context for next story
            if result:
                short_summary = f"Story {idx}: "
                if result.get("is_relevant"):
                    short_summary += f"Relevant ({result.get('content_type')}). Brand: {result.get('brand')}. Desc: {result.get('Description')}"
    head = f"[{ts}] [{level}]"
    if obj is not None:
        try:
            print(head, *args)
            print(json.dumps(obj, ensure_ascii=False))
            return
        except Exception:
            pass
    print(head, *args)


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    payload = r.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = None
    if isinstance(payload, dict):
        # העדיפי ערכים מה-payload אם חסרים ברמה העליונה
        for k in (
            "type",
            "urls",
            "user_id",
            "hashtags",
            "mentions",
            "ocr_text",
            "stickers",
            "username",
            "image_url",
            "permalink",
            "video_url",
            "media_meta",
            "frames_used",
            "caption_text",
            "content_hash",
            "source_flags",
            "taken_at_iso",
            "language_guess",
            "ocr_confidence",
            "expiring_at_iso",
            "brand_candidates",
            "raw_text_candidates",
        ):
            if r.get(k) is None and payload.get(k) is not None:
                r[k] = payload.get(k)
    
    # ✅ FIX: Extract user_id from media_id if missing
    if not r.get("user_id"):
        media_id = r.get("media_id", "")
        if "_" in media_id:
            # Format: {media_id}_{user_id}
            parts = media_id.split("_")
            if len(parts) >= 2:
                r["user_id"] = parts[-1]  # Last part is user_id
    
    return r


BAD_IMAGE_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "cdninstagram.com",
    "scontent.cdninstagram.com",
    "fbcdn.net",
    "fna.fbcdn.net",
}


def _short(s, n=1000):
    try:
        s = str(s)
        s = re.sub(r"\s+", " ", s)
        return s[:n]
    except Exception:
        return s



def load_id_to_username_map():
    """
    Loads the latest user_ids_*.json file to create a mapping of user_id -> username.
    This ensures we use the correct influencer name even if the story data is missing it.
    """
    try:
        files = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'user_ids_*.json'))
        if not files:
            print("⚠️ No user_ids_*.json files found for username mapping.")
            return {}
        
        latest_file = max(files, key=os.path.getctime)
        print(f"📂 Loading username mapping from: {latest_file}")
        
        with open(latest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Map user_id -> username
            mapping = {}
            # Handle both list and dict with "results" key
            items = data.get("results", []) if isinstance(data, dict) else data
            
            if isinstance(items, list):
                for u in items:
                    if isinstance(u, dict):
                        uid = str(u.get("user_id") or "")
                        uname = u.get("username")
                        if uid and uname:
                            mapping[uid] = uname
            
            print(f"✅ Loaded {len(mapping)} username mappings.")
            return mapping
    except Exception as e:
        print(f"❌ Error loading username map: {e}")
        return {}


def main():
    # Load username mapping first
    id_map = load_id_to_username_map()

    try:
        rows = get_raw_rows(MAX_ROWS)
    except Exception as e:
        print("❌ Failed to fetch rows:", e)
        return
    dlog(
        "SB auth header startswith 'eyJ'? (anon-ish):",
        obj=str(SB_HEADERS.get("Authorization", ""))[:10],
    )

    # 1. Group rows by user_id
    rows_by_user = {}
    for r in rows:
        raw_uid = r.get("user_id")
        if raw_uid is None or raw_uid == "":
            print(f"⚠️ WARNING: Story {r.get('media_id')} has no user_id! Row: {r.keys()}")
        uid = str(raw_uid or "unknown")
        if uid not in rows_by_user:
            rows_by_user[uid] = []
        rows_by_user[uid].append(r)

    print(f"Fetched {len(rows)} rows, grouped into {len(rows_by_user)} users.")

    # 2. Process each user's stories chronologically
    for uid, user_rows in rows_by_user.items():
        # Sort by date (oldest first) so context builds up naturally
        user_rows.sort(key=lambda x: x.get("taken_at_iso") or "")
        
        # Get username for logging
        username = "unknown"
        if user_rows:
             # Try to resolve username from the first row using our map or row data
            _dummy_res = {} # helper to use _safe_name logic
            username = _safe_name(user_rows[0], _dummy_res)
            if uid in id_map:
                username = id_map[uid]

        print(f"\n👤 Processing User: {username} ({len(user_rows)} stories)")
        
        # Context buffer for this user's session
        user_context = []
        
        # Session Deduplication Set
        session_dedup = set()

        for idx, row in enumerate(user_rows, 1):
            media_id = row.get("media_id") or f"row_{idx}"
            print(f"  👉 Story {idx}/{len(user_rows)}: {media_id}")
            
            # Normalize
            row = normalize_row(row)
            
            # Update uid with the normalized user_id (might have been extracted from media_id)
            uid = str(row.get("user_id") or "unknown")
            
            # Ensure username is correct
            if uid in id_map:
                row["username"] = id_map[uid]
                if isinstance(row.get("payload"), dict):
                    row["payload"]["username"] = id_map[uid]
                print(f"    ✅ Mapped user_id {uid} -> {id_map[uid]}")
            else:
                print(f"    ⚠️ user_id {uid} not found in mapping (will be 'unknown')")

            # Check if already processed
            try:
                if already_in_relevant(media_id):
                    print("    ↩️ Skipped (already relevant)")
                    # Add to context anyway so subsequent stories know about it? 
                    # For now, we skip fully to save time, but ideally we'd load its result.
                    continue
            except Exception:
                pass

            # Prepare context string
            context_str = ""
            if user_context:
                context_str = "\n".join([f"- {c}" for c in user_context[-3:]]) # Last 3 stories

            # Call OpenAI with Context
            try:
                result = call_openai_filter(row, context_str=context_str)
                print(f"DEBUG: OpenAI returned type: {type(result)}")
                if result:
                    print(f"DEBUG: OpenAI is_relevant: {result.get('is_relevant')}")
            except Exception as e:
                print("    ❌ OpenAI failed:", e)
                set_processing_status(media_id, "error", str(e))
                continue
            
            # ... (Rest of the logic: Fallback, Upsert, etc.) ...
            
            # Update Context for next story
            if result:
                short_summary = f"Story {idx}: "
                if result.get("is_relevant"):
                    short_summary += f"Relevant ({result.get('content_type')}). Brand: {result.get('brand')}. Desc: {result.get('Description')}"
                else:
                    short_summary += "Not relevant (Organic/Other)."
                user_context.append(short_summary)


            # ✅ הוספה: fallback אם הקטגוריה חסרה
            if result:
                if not result.get("category") or result.get("category") in (
                    None,
                    "",
                    "null",
                ):
                    result["category"] = infer_category_from_text_or_image(row, result)

            # --- Fallback מקומי מהסטיקרים (גם אם המודל החזיר משהו וגם אם לא) ---
            local_items = extract_pairs_from_stickers(row)

            if not result:
                result = {}

            # אם המודל לא סימן רלוונטי אבל נמצאו קופונים מקומית -> כן רלוונטי
            if not bool(result.get("is_relevant")) and local_items:
                result["is_relevant"] = True
                result["content_type"] = "coupon"

            # מזג/דדופ של פריטי הקופונים
            merged_items = (result.get("coupon_items") or []) + local_items
            if merged_items:
                seen = set()
                dedup = []
                for it in merged_items:
                    brand = (it.get("brand") or "unknown").strip().lower()
                    code = (it.get("code") or "").strip().upper()
                    if not code:
                        continue
                    key = (brand, code)
                    if key in seen:
                        continue
                    seen.add(key)
                    dedup.append(
                        {
                            "brand": brand,
                            "code": code,
                            "source": (it.get("source") or "ocr").lower(),
                            "snippet": it.get("snippet"),
                        }
                    )
                result["coupon_items"] = dedup
                result["coupons"] = sorted({it["code"] for it in dedup})
                # אם אין קוד ראשי – קחי את הראשון
                # אם אין קוד ראשי – קחי את הראשון
                if not result.get("coupon") and result["coupons"]:
                    result["coupon"] = result["coupons"][0]

            # כלל ביטחון: אם יש קופונים כלשהם -> רלוונטי
            if not result.get("is_relevant") and (
                result.get("coupons") or result.get("coupon_items")
            ):
                result["is_relevant"] = True
                result["content_type"] = "coupon"

            # STRICT FILTERING:
            ctype = result.get("content_type")
            
            if result.get("is_relevant"):
                try:
                    cat = (result.get("category") or "").strip().lower()
                    # FORCE rejection of "other" if we can infer something better
                    if cat in ("", "null", "none", "undefined", "other"):
                        inferred = infer_category_from_text_or_image(row, result)
                        if inferred != "other":
                            cat = inferred
                    result["category"] = cat or "other"
                    
                    # --- SESSION DEDUPLICATION ---
                    brand = (result.get("brand") or "unknown").strip().lower()
                    
                    dedup_token = None
                    if ctype == "coupon":
                        dedup_token = (result.get("coupon") or "").strip().upper()
                    elif ctype == "collab_ad":
                        u = result.get("url") or ""
                        try:
                            parsed = urlparse(u)
                            dedup_token = f"{parsed.netloc}{parsed.path}"
                        except:
                            dedup_token = u
                    elif ctype == "recommendation":
                        dedup_token = "rec"
                    
                    dedup_key = (ctype, brand, dedup_token)
                    
                    if dedup_key in session_dedup:
                        print(f"    🔄 Duplicate in session: {dedup_key} - SKIPPING DB INSERT")
                        set_processing_status(media_id, "skipped", None, {"reason": "duplicate_in_session"})
                        continue
                    
                    session_dedup.add(dedup_key)
                    # -----------------------------

                    # DEBUG: Print description
                    desc = result.get("Description")
                    print(f"🚀 ENTERING RELEVANT BLOCK for {media_id}")
                    print(f"📝 Description found: {desc}")
                    print(f"🏷️ Content Type: {ctype}")

                    if ctype == "recommendation":
                        # Save to recommendations table
                        upsert_recommendation(row, result)
                        set_processing_status(media_id, "ok", None, {"decision": "recommendation"})
                        print("✅ Inserted into story_recommendations")
                    
                    elif ctype in ("coupon", "collab_ad"):
                        # Save to relevant_story table
                        if ctype == "collab_ad":
                            result["coupon"] = None
                            result["coupons"] = []
                            result["coupon_items"] = []
                        
                        upsert_relevant(row, result)
                        set_processing_status(media_id, "ok", None, {"decision": "relevant"})
                        print("✅ Inserted into relevant_story")
                    
                    else:
                        print(f"ℹ️ Unknown relevant type '{ctype}' - skipping")
                        set_processing_status(media_id, "skipped", None, {"reason": "unknown_type"})

                except Exception as e:
                    print("❌ Insert/Upsert failed:", e)
                    set_processing_status(media_id, "error", str(e))
            else:
                print("ℹ️ Not relevant")
                # Save the AI result in the processing field so we can see WHY it was rejected
                set_processing_status(media_id, "non_relevant", None, {"ai_result": result})


def generate_performance_report():
    """מייצר קובץ JSON עם ניתוח מפורט לכל משפיענית"""
    print("\n📊 מייצר דוח ביצועים...")
    
    try:
        # טוען את המיפוי
        id_map = load_id_to_username_map()
        
        # שולף נתונים מהדאטה-בייס
        print("   📥 שולף נתונים מהדאטה-בייס...")
        
        # כל הסטוריז
        url = f"{SUPABASE_REST}/{RAW_TABLE}"
        r = sb_get(url, {"select": "user_id,media_id", "limit": "10000"})
        all_stories = r.json() if r.status_code == 200 else []
        
        # קופונים ולינקים
        url = f"{SUPABASE_REST}/{REL_TABLE}"
        r = sb_get(url, {"select": "user_id,name,url,coupon", "limit": "10000"})
        relevant = r.json() if r.status_code == 200 else []

        # מבנה נתונים
        from collections import defaultdict
        stats = defaultdict(lambda: {
            "total": 0,
            "coupons": 0,
            "links": 0,
            "organic": 0,
        })
        
        # ספירה
        for story in all_stories:
            uid = str(story.get("user_id", "unknown"))
            stats[uid]["total"] += 1
        
        for story in relevant:
            uid = str(story.get("user_id", "unknown"))
            if story.get("url"):
                stats[uid]["links"] += 1
            else:
                stats[uid]["coupons"] += 1

        # organic = סטוריז שה-processing שלהם non_relevant
        for story in all_stories:
            uid = str(story.get("user_id", "unknown"))
            processing = story.get("processing") or {}
            if isinstance(processing, dict) and processing.get("status") == "non_relevant":
                stats[uid]["organic"] += 1
        
        # יצירת הדוח
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_influencers": len(stats),
            "total_stories": sum(s["total"] for s in stats.values()),
            "influencers": []
        }
        
        for uid, data in stats.items():
            username = id_map.get(uid, f"user_{uid}")
            total = data["total"]
            relevant_count = data["coupons"] + data["links"] + data["recommendations"]
            
            # חישוב ציון (0-100)
            if total > 0:
                score = int((relevant_count / total) * 100)
            else:
                score = 0
            
            # דירוג
            if score >= 70:
                grade = "A"
                emoji = "🟢"
            elif score >= 50:
                grade = "B"
                emoji = "🟡"
            elif score >= 30:
                grade = "C"
                emoji = "🟠"
            else:
                grade = "D"
                emoji = "🔴"
            
            report["influencers"].append({
                "username": username,
                "user_id": uid,
                "score": score,
                "grade": grade,
                "emoji": emoji,
                "stats": {
                    "total_stories": total,
                    "coupons": data["coupons"],
                    "links": data["links"],
                    "recommendations": data["recommendations"],
                    "organic": data["organic"],
                    "relevant_count": relevant_count,
                    "relevant_percentage": round((relevant_count / total * 100) if total > 0 else 0, 1)
                }
            })
        
        # מיון לפי ציון
        report["influencers"].sort(key=lambda x: x["score"], reverse=True)
        
        # שמירה לקובץ
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"influencer_report_{timestamp}.json"
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"   ✅ דוח נשמר ב: {filename}")
        
        # הדפסת סיכום
        print("\n" + "=" * 100)
        print("📊 סיכום ביצועים")
        print("=" * 100)
        print(f"סה\"כ משפיעניות: {report['total_influencers']}")
        print(f"סה\"כ סטוריז: {report['total_stories']}")
        print(f"\nטופ 5 משפיעניות:")
        for inf in report["influencers"][:5]:
            print(f"  {inf['emoji']} {inf['username']:<25} ציון: {inf['score']}/100 ({inf['grade']}) - {inf['stats']['relevant_count']}/{inf['stats']['total_stories']} רלוונטיים")
        
        if len(report["influencers"]) > 5:
            print(f"\n💡 לדוח מלא, פתחי את: {filename}")
        
        print("=" * 100 + "\n")
        
    except Exception as e:
        print(f"   ⚠️ שגיאה ביצירת דוח: {e}")



def main_v2():
    """
    Improved main function with statistics, validation, and dry-run support.
    """
    # Initialize statistics (IMPROVEMENT #4)
    stats = RunStatistics()
    logger.info(f"Starting run_daily.py (Improvements enabled, DRY_RUN={DRY_RUN})")

    # Initialize Supabase Client for Sidecar
    sb_client = None
    if SIDECAR_ENABLED and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
             sb_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
             print("✅ Sidecar: Supabase client initialized")
        except Exception as e:
             print(f"⚠️ Sidecar: Failed to init Supabase client: {e}")

    # Load username mapping first
    id_map = load_id_to_username_map()

    try:
        raw_rows = get_raw_rows(MAX_ROWS)
        # Normalize immediately to fix user_ids
        rows = [normalize_row(r) for r in raw_rows]
    except Exception as e:
        logger.error(f"❌ Failed to fetch rows: {e}")
        return
    dlog(
        "SB auth header startswith 'eyJ'? (anon-ish):",
        obj=str(SB_HEADERS.get("Authorization", ""))[:10],
    )

    # 1. Group rows by user_id
    rows_by_user = {}
    for r in rows:
        raw_uid = r.get("user_id")
        if raw_uid is None or raw_uid == "":
            print(f"⚠️ WARNING: Story {r.get('media_id')} has no user_id! Row: {r.keys()}")
        uid = str(raw_uid or "unknown")
        if uid not in rows_by_user:
            rows_by_user[uid] = []
        rows_by_user[uid].append(r)

    print(f"Fetched {len(rows)} rows, grouped into {len(rows_by_user)} users.")

    # 2. Process each user's stories chronologically
    for uid, user_rows in rows_by_user.items():
        # Sort by date (oldest first) so context builds up naturally
        user_rows.sort(key=lambda x: x.get("taken_at_iso") or "")
        
        # Get username for logging
        username = "unknown"
        if user_rows:
             # Try to resolve username from the first row using our map or row data
            _dummy_res = {} # helper to use _safe_name logic
            username = _safe_name(user_rows[0], _dummy_res)
            if uid in id_map:
                username = id_map[uid]

        print(f"\n👤 Processing User: {username} ({len(user_rows)} stories)")
        
        # ✅ OPTIMIZATION: Load all processed IDs for this user ONCE
        processed_ids = load_all_processed_ids(uid)
        print(f"   📋 Already processed: {len(processed_ids)} stories")
        
        # Context buffer for this user's session
        user_context = []
        
        # Session Deduplication Set
        session_dedup = set()

        # ✅ PROGRESS BAR: Show processing progress
        pbar = tqdm(enumerate(user_rows, 1), total=len(user_rows), desc=f"Processing {username}", unit="story")
        for idx, row in pbar:
            media_id = row.get("media_id") or f"row_{idx}"
            pbar.set_postfix({"story": media_id[:20]})
            
            # Normalize
            row = normalize_row(row)
            
            # Update uid with the normalized user_id (might have been extracted from media_id)
            uid = str(row.get("user_id") or "unknown")
            
            # Ensure username is correct
            if uid in id_map:
                row["username"] = id_map[uid]
                if isinstance(row.get("payload"), dict):
                    row["payload"]["username"] = id_map[uid]
                # print(f"    ✅ Mapped user_id {uid} -> {id_map[uid]}")
            
            # ✅ OPTIMIZED: Check if already processed (O(1) set lookup instead of 2 API calls!)
            if media_id in processed_ids:
                print("    ↩️ Skipped (already processed)")
                continue

            # Prepare context string
            context_str = ""
            if user_context:
                context_str = "\n".join([f"- {c}" for c in user_context[-3:]]) # Last 3 stories

            # --- SIDECAR EXECUTION (ACTIVE MODE) ---
            # Default result structure (for fallback or organic)
            result = {
                "is_relevant": False, 
                "content_type": "organic",
                "confidence": 0.0
            }
            
            if sb_client:
                try:
                    # 1. Analyze with Sidecar (Vector + Small Code)
                    sidecar_start = time.time()
                    sidecar_res = analyze_story_sidecar(row, sb_client)
                    sidecar_time = time.time() - sidecar_start
                    stats.add_processing_time(sidecar_time)
                    
                    # 2. Map Sidecar Result to Legacy Format
                    sr = sidecar_res['result']
                    verdict = sr.get('verdict', 'organic')
                    
                    # Map verdict to legacy content_type
                    if verdict == 'coupon':
                        result['is_relevant'] = True
                        result['content_type'] = 'coupon'
                        result['coupon'] = sr.get('code')
                        result['coupons'] = [sr.get('code')] if sr.get('code') else []
                        result['url'] = sr.get('link')
                    elif verdict == 'collab_ad':
                        result['is_relevant'] = True
                        result['content_type'] = 'collab_ad'
                        result['url'] = sr.get('link')
                    elif verdict == 'recommendation':
                        result['is_relevant'] = True
                        result['content_type'] = 'recommendation'
                    
                    # Common fields
                    result['brand'] = sr.get('brand')
                    result['Description'] = sr.get('description', '')
                    result['category'] = sr.get('category', 'other')
                    result['confidence'] = sr.get('confidence', 0.0)
                    
                    # Cost Tracking (Approximate)
                    # Embedding: ~$0.00002 | Mini Input (1k): $0.00015 | Mini Output (100): $0.00006 
                    sidecar_cost = 0.0003 
                    stats.add_cost(sidecar_cost)
                    
                    print(f"    🤖 Sidecar: {verdict} (Conf: {result['confidence']:.2f})")
                    print(f"    📝 {sr.get('description')}")
                    print(f"    📂 {sr.get('category')}")

                    # 3. Save to Memory (Learning Loop)
                    # Only save if confidence is high to avoid poisoning the well
                    if result['confidence'] > 0.8:
                        save_to_memory(
                            sb_client, 
                            media_id,
                            uid, 
                            sidecar_res['context'],
                            sidecar_res['vector'],
                            sidecar_res['result'],
                            is_ground_truth=False, # We don't know for sure without manual review
                            verification_source="sidecar_v1"
                        )

                except Exception as e:
                    print(f"    ❌ Sidecar Failed: {e}")
                    stats.add_error()
                    # Fallback Logic (Optional: Call legacy if sidecar dies? For now, just skip)
                    set_processing_status(media_id, "error", f"Sidecar failed: {e}")
                    continue
            else:
                 print("    ⚠️ Sidecar disabled or client missing. Skipping story.")
                 continue

            # --- Result Handling (Legacy DB Logic) ---
            if result and result.get("is_relevant"):
                # VALIDATE RESULT
                result = validate_ai_result(result, row)
                if not result.get("is_relevant"):
                     # If validation turned it non-relevant
                     print("ℹ️ Validation marked as not relevant")
                     stats.add_story(username, "organic")
                     set_processing_status(media_id, "non_relevant", None, {"ai_result": result, "reason": "validation_rejected"})
                     continue

                ctype = result.get("content_type")
                
                # --- SESSION DEDUPLICATION ---
                brand = (result.get("brand") or "unknown").strip().lower()
                dedup_token = None
                if ctype == "coupon":
                    dedup_token = (result.get("coupon") or "").strip().upper()
                elif ctype == "collab_ad":
                     u = result.get("url") or ""
                     try:
                         parsed = urlparse(u)
                         dedup_token = f"{parsed.netloc}{parsed.path}"
                     except:
                         dedup_token = u
                elif ctype == "recommendation":
                    dedup_token = "rec"
                
                dedup_key = (ctype, brand, dedup_token)
                
                if dedup_key in session_dedup:
                    print(f"    🔄 Duplicate in session: {dedup_key} - SKIPPING DB INSERT")
                    set_processing_status(media_id, "skipped", None, {"reason": "duplicate_in_session"})
                    continue
                
                session_dedup.add(dedup_key)
                
                # DEBUG: Print description
                desc = result.get("Description")
                print(f"🚀 ENTERING RELEVANT BLOCK for {media_id}")
                # print(f"📝 Description found: {desc}")
                print(f"🏷️ Content Type: {ctype}")

                # Update Stats
                stats.add_story(username, ctype, has_coupon=bool(result.get("coupon")))

                # --- DB UPSERT (With DRY_RUN check) ---
                try:
                    if ctype == "recommendation":
                        if DRY_RUN:
                            print(f"🔍 DRY RUN: Would insert recommendation for {media_id}")
                        else:
                            upsert_recommendation(row, result)
                            set_processing_status(media_id, "ok", None, {"decision": "recommendation"})
                            print("✅ Inserted into story_recommendations")
                    
                    elif ctype in ("coupon", "collab_ad"):
                        if ctype == "collab_ad":
                            result["coupon"] = None
                            result["coupons"] = []
                            result["coupon_items"] = []
                        
                        if DRY_RUN:
                            print(f"🔍 DRY RUN: Would insert relevant story for {media_id}")
                        else:
                            upsert_relevant(row, result)
                            set_processing_status(media_id, "ok", None, {"decision": "relevant"})
                            print("✅ Inserted into relevant_story")
                    else:
                        print(f"ℹ️ Unknown relevant type '{ctype}' - skipping")
                        set_processing_status(media_id, "skipped", None, {"reason": "unknown_type"})

                except Exception as e:
                    print("❌ Insert/Upsert failed:", e)
                    set_processing_status(media_id, "error", str(e))
            else:
                print("ℹ️ Not relevant (Organic)")
                stats.add_story(username, "organic")
                set_processing_status(media_id, "non_relevant", None, {"ai_result": result})



            # Update Context for next story
            if result:
                short_summary = f"Story {idx}: "
                if result.get("is_relevant"):
                     short_summary += f"Relevant ({result.get('content_type')}). Brand: {result.get('brand')}. Desc: {result.get('Description')}"
                else:
                    short_summary += "Not relevant (Organic/Other)."
                user_context.append(short_summary)

    # End of run - Print Statistics
    stats.print_summary()


if __name__ == "__main__":
    # main()
    main_v2()
    # generate_performance_report() # סטטיסטיקות כבר מודפסות ב-main_v2
