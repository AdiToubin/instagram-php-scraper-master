import os
import sys
import hashlib
from datetime import datetime, timedelta, timezone

# Force UTF-8 encoding for Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
import re, json, time, base64, mimetypes, glob
import requests
from requests.exceptions import Timeout, RequestException
from dotenv import load_dotenv
import logging
from collections import defaultdict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tqdm import tqdm


load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY")

RAW_TABLE = "story_raw"
REL_TABLE = "relevant_story"
REC_TABLE = "story_recommendations"
MODEL = "gemini-2.5-pro"
MAX_ROWS = 1200

SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
SB_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
    "Accept": "application/json",
}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_HEADERS = {
    "Authorization": f"Bearer {GEMINI_API_KEY}",
    "Content-Type": "application/json",
}

MAX_IMAGE_BYTES = 8 * 1024 * 1024

# ============== LOGGING SETUP ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs', 'run_daily.log'), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
DEBUG = True


# ============== STATISTICS ==============
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
        self.influencer_stats = defaultdict(lambda: {
            'total': 0, 'coupons': 0, 'collab_ads': 0, 'recommendations': 0, 'organic': 0
        })

    def add_story(self, username, content_type):
        self.total_stories += 1
        self.influencer_stats[username]['total'] += 1
        if content_type == 'coupon':
            self.coupons_found += 1
            self.relevant_found += 1
            self.influencer_stats[username]['coupons'] += 1
        elif content_type == 'collab_ad':
            self.collab_ads_found += 1
            self.relevant_found += 1
            self.influencer_stats[username]['collab_ads'] += 1
        elif content_type == 'recommendation':
            self.recommendations_found += 1
            self.influencer_stats[username]['recommendations'] += 1
        else:
            self.influencer_stats[username]['organic'] += 1

    def add_processing_time(self, seconds):
        self.processing_times.append(seconds)

    def add_cost(self, cost):
        self.total_cost += cost

    def add_error(self):
        self.errors += 1

    def print_summary(self):
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
        print("\n" + "="*70)
        print("👥 INFLUENCER STATISTICS")
        print("="*70)
        print(f"{'Username':<20} {'Total':>6} {'Coupons':>8} {'Ads':>6} {'Recs':>6} {'Organic':>8} {'Commercial %':>13}")
        print("-"*70)
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
            commercial_pct = commercial / total * 100
            print(f"{username:<20} {total:>6} {stats['coupons']:>8} {stats['collab_ads']:>6} "
                  f"{stats['recommendations']:>6} {stats['organic']:>8} {commercial_pct:>12.1f}%")
        print("="*70)


# ============== SMALL HELPERS ==============

def dlog(*args, level="INFO", obj=None):
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


def _normalize_brand_from_host(host: Optional[str]) -> Optional[str]:
    if not host:
        return None
    h = host.lower().replace("www.", "")
    parts = re.split(r"[.\-]+", h)
    bad = {"com", "co", "il", "net", "org", "instagram", "cdninstagram", "fbcdn", "fna", "scontent"}
    cand = [p for p in parts if p and p not in bad]
    return cand[0] if cand else None


def _safe_brand(row: Dict[str, Any], result: Dict[str, Any]) -> str:
    raw = result.get("main_brand") or result.get("brand") or ""
    b = raw.strip() if isinstance(raw, str) else None
    if b:
        return b
    urls = (result.get("urls") or []) + (row.get("urls") or [])
    for u in urls:
        try:
            host = urlparse(u).hostname
        except Exception:
            host = None
        b2 = _normalize_brand_from_host(host)
        if b2:
            return b2
    for t in result.get("brand_tokens") or []:
        t = str(t).strip().lower()
        if t:
            return t
    return "unknown"


def _safe_name(row: Dict[str, Any], result: Dict[str, Any]) -> str:
    candidates = [result.get("name"), row.get("username"), row.get("name")]
    for c in candidates:
        if c and str(c).strip() and not str(c).strip().isdigit():
            return str(c).strip()
    user_id = row.get("user_id")
    if user_id:
        return f"user_{user_id}"
    return "unknown"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============== SUPABASE HTTP HELPERS ==============

def sb_get(path: str, params: Dict[str, str]) -> requests.Response:
    return requests.get(path, headers=SB_HEADERS, params=params, timeout=30)


def sb_patch(path: str, params: Dict[str, str], body: Dict[str, Any]) -> requests.Response:
    return requests.patch(path, headers=SB_HEADERS, params=params, data=json.dumps(body), timeout=30)


def sb_post(path: str, body: Dict[str, Any], prefer: Optional[str] = None,
            params: Optional[Dict[str, str]] = None) -> requests.Response:
    headers = SB_HEADERS.copy()
    if prefer:
        headers["Prefer"] = prefer
    return requests.post(path, headers=headers, params=params, data=json.dumps(body), timeout=30)


def get_raw_rows(limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {
        "select": "*",
        "order": "inserted_at.desc",
        "limit": str(limit),
        "processing->>status": "is.null",
    }
    r = sb_get(url, params)
    if r.status_code >= 400:
        print("❌ Supabase error (raw fetch):", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


# ============== DB WRITE HELPERS ==============

def set_processing_status(media_id: str, status: str, last_error: Optional[str] = None,
                          extra: Optional[Dict[str, Any]] = None):
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {"media_id": f"eq.{media_id}"}
    payload = {"processing": {"status": status, "last_error": last_error, "ts": now_iso(), **(extra or {})}}
    r = sb_patch(url, params, payload)
    if r.status_code >= 400:
        print("⚠️ set_processing_status failed:", r.status_code, r.text)


def upsert_relevant(row: Dict[str, Any], result: Dict[str, Any]):
    media_id = row.get("media_id")
    ctype = result.get("content_type", "unknown")
    brand = _safe_brand(row, result)
    logger.info(f"→ upsert_relevant called: media_id={media_id}, ctype={ctype}, brand={brand}")

    content_hash = generate_coupon_hash(result)
    user_id = row.get("user_id")

    dup = is_duplicate_in_timewindow(user_id, content_hash, hours=48)
    logger.info(f"→ is_duplicate_in_timewindow={dup} for media_id={media_id}, hash={content_hash}")
    if dup:
        logger.info(f"⏭️ Skipping duplicate: {brand}")
        return None

    items = result.get("coupon_items") or []
    seen = set()
    dedup = []
    for it in items:
        b = (it.get("brand") or "unknown").strip().lower()
        code = (it.get("coupon_code") or it.get("code") or "").strip().upper()
        if not code:
            continue
        key = (b, code)
        if key in seen:
            continue
        seen.add(key)
        dedup.append({"brand": b, "code": code,
                      "source": (it.get("source") or "unknown").lower(),
                      "snippet": it.get("evidence_snippet") or it.get("snippet")})

    payload = {
        "name": _safe_name(row, result),
        "user_id": user_id,
        "brand": brand,
        "coupon": result.get("main_coupon") or result.get("coupon") or None,
        "url": result.get("main_url") or result.get("url"),
        "date": row.get("taken_at_iso"),
        "description": (
            (result.get("description_he") or result.get("Description") or "").strip()
            or (row.get("caption_text") or "").strip()
            or (row.get("ocr_text") or "").strip()
            or None
        ),
    }
    logger.info(f"→ POSTing to {REL_TABLE}: media_id={media_id}, brand={payload.get('brand')}, coupon={payload.get('coupon')}, url={str(payload.get('url',''))[:80]}")
    db_url = f"{SUPABASE_REST}/{REL_TABLE}"
    try:
        r = sb_post(db_url, payload, prefer="return=representation")
    except Exception as net_err:
        logger.error(f"❌ upsert relevant network error: {net_err}")
        raise
    logger.info(f"→ relevant_story response: status={r.status_code}, body={r.text[:300]}")
    if r.status_code == 409:
        logger.info(f"→ Brand already in relevant_story (brand+user constraint): {brand}/{user_id}")
        return None
    if r.status_code >= 400:
        logger.error(f"❌ upsert relevant failed: {r.status_code} {r.text}")
        r.raise_for_status()
    return True


# ============== COUPON / BRAND CONSTANTS ==============

BRAND_MAP_HE_IL = {
    "קולנטה": "kolenta", "מילא": "mila", "נעמה": "naama",
    "פלטין אקספרס": "platinexpress", "הום סטייל": "homestyle",
    "חני וינברגר": "chanivainberger", "מהדרין אונליין": "mehadrinonline",
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
    "QA", "KW", "OM", "BH", "JO", "LB", "EG", "MA", "DZ", "TN", "LY", "SD", "YE", "SY",
    "ADDICT", "DRESS", "NOIR", "CAFENOIR", "BAUM", "ORIN", "ORINDRESS", "TINC", "TINCE", "HANA", "CAFE",
}


# ============== VALIDATION ==============

def is_valid_coupon_code(code: str, username: str = "") -> bool:  # noqa: ARG001 (username reserved for future use)
    if not code or len(code) < 4 or len(code) > 20:
        return False
    code_upper = code.upper()
    if code_upper in IGNORE_COUPONS:
        return False
    if re.match(r'^\d{2}-\d{2}$', code_upper):
        return False
    if re.match(r'^[0-9]+[A-Z]+[0-9]+[A-Z]+', code_upper):
        return False
    # Short all-numeric codes (4-6 digits) are valid promo codes (e.g. 6868); longer ones are phone numbers
    if code_upper.replace("-", "").isdigit():
        return len(code_upper.replace("-", "")) <= 6
    if sum(1 for c in code_upper if c.isalpha()) < 2:
        return False
    return True


def validate_ai_result(result, row):
    if not result:
        return result
    username = row.get('username', '')

    if result.get('main_coupon'):
        code = result['main_coupon'].upper()
        if not is_valid_coupon_code(code, username):
            logger.warning(f"AI returned invalid main_coupon '{code}', removing")
            result['main_coupon'] = None

    valid_items = []
    for it in (result.get('coupon_items') or []):
        code = (it.get('coupon_code') or '').upper()
        if code and not is_valid_coupon_code(code, username):
            logger.warning(f"AI returned invalid coupon_code '{code}' in coupon_items, removing")
            it['coupon_code'] = None
        valid_items.append(it)
    result['coupon_items'] = valid_items

    if not result.get('category') or result.get('category') in ('', 'null', 'none'):
        result['category'] = 'other'
    if result.get('main_brand') in ('General', 'Unknown', 'Brand', '', None):
        result['main_brand'] = 'unknown'

    return result


# ============== DEDUPLICATION ==============

def generate_coupon_hash(result: Dict[str, Any]) -> str:
    components = [
        (result.get('main_coupon') or result.get('code') or '').upper(),
        (result.get('main_brand') or result.get('brand') or '').lower(),
        result.get('main_url') or result.get('url') or '',
        result.get('content_type') or result.get('verdict') or '',
    ]
    content = '|'.join(str(c) for c in components if c)
    return hashlib.md5(content.encode()).hexdigest()[:16]


def is_duplicate_in_timewindow(user_id: str, content_hash: str, hours: int = 48) -> bool:
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


# ============== PROCESSED-ID TRACKING ==============

def load_all_processed_ids(user_id: Optional[str] = None) -> set:
    processed_ids = set()
    try:
        url = f"{SUPABASE_REST}/{RAW_TABLE}"
        params = {"select": "media_id", "processing->>status": "not.is.null"}
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
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {"select": "processing", "media_id": f"eq.{media_id}", "limit": "1"}
    r = sb_get(url, params)
    if r.status_code == 200 and r.json():
        processing = r.json()[0].get("processing")
        if isinstance(processing, dict) and processing.get("status") in ("ok", "done", "non_relevant", "skipped"):
            return True
    return False


# ============== ACTIVE LEARNING ==============

def save_correction(media_id: str, user_id: str, ai_result: Dict[str, Any],
                    correct_verdict: str, correct_code: Optional[str] = None,
                    correct_brand: Optional[str] = None, story_context: Optional[str] = None,
                    note: Optional[str] = None) -> Optional[requests.Response]:
    payload = {
        "media_id": media_id, "user_id": user_id,
        "ai_verdict": ai_result.get("verdict"), "ai_code": ai_result.get("code"),
        "ai_brand": ai_result.get("brand"), "ai_confidence": ai_result.get("confidence", 0.0),
        "correct_verdict": correct_verdict, "correct_code": correct_code,
        "correct_brand": correct_brand, "story_context": story_context, "correction_note": note,
    }
    try:
        url = f"{SUPABASE_REST}/user_corrections"
        r = sb_post(url, payload,
                    prefer="return=representation,resolution=merge-duplicates",
                    params={"on_conflict": "media_id"})
        if r.status_code >= 400:
            logger.error(f"Failed to save correction: {r.status_code} {r.text}")
            return None
        logger.info(f"✅ Correction saved for {media_id}: {correct_verdict}")
        return r
    except Exception as e:
        logger.error(f"Error saving correction: {e}")
        return None


def load_user_corrections(user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    try:
        url = f"{SUPABASE_REST}/user_corrections"
        params = {"select": "*", "user_id": f"eq.{user_id}", "order": "corrected_at.desc", "limit": str(limit)}
        r = sb_get(url, params)
        if r.status_code == 200:
            corrections = r.json()
            logger.info(f"📚 Loaded {len(corrections)} corrections for user {user_id}")
            return corrections
        logger.warning(f"Failed to load corrections: {r.status_code}")
        return []
    except Exception as e:
        logger.error(f"Error loading corrections: {e}")
        return []


# ============== TEXT / URL ANALYSIS ==============

SYSTEM_PROMPT = """You are Dealink AI — an expert Instagram commercial-content analyzer for Israeli influencers.

Your job is to analyze ONE Instagram story/reel/post at a time and extract structured coupon, affiliate, brand, and commercial-offer data.

You receive raw data from Instagram, possibly including:
- OCR text from image/video frames
- Caption text
- Sticker text
- Link sticker URLs
- Hashtags
- Mentions
- Image/video visual context
- Influencer username/profile metadata

You must output VALID JSON ONLY.
No markdown.
No explanations.
No text outside JSON.

==================================================
MAIN GOAL
==================================================

Detect whether the content contains any commercial value:
1. Coupon code
2. Affiliate/referral/purchase link
3. Brand collaboration
4. Product recommendation
5. Sale/discount campaign
6. Multiple coupons/brands in one story

The highest priority is NOT missing real coupons.
But you must avoid inventing data.

==================================================
RELEVANCE RULES
==================================================

Set "is_relevant": true if at least one of these exists:

A. COUPON
A visible or strongly implied coupon code appears near words like:
Hebrew: קוד, קופון, קוד קופון, הנחה, הטבה, מבצע, סייל, קוד שלי, עם הקוד, תזינו, בקופה, בהזמנה, למימוש
English: code, coupon, promo, discount, sale, use code, checkout, voucher, deal

Examples of valid coupon codes: ADI10, SIVAN20, MAYA15, DEAL20, NOA-10, SAVE30, SPRING25

B. AFFILIATE / PURCHASE LINK
There is a link sticker, URL, swipe-up style link, Bitly/Linktree/Affiliate link, or "link in bio" with commercial intent.

C. BRAND COLLAB / AD
The influencer promotes a product, brand, collection, launch, campaign, or sale, even without coupon.

D. RECOMMENDATION
A brand/product is clearly recommended or displayed with product focus, even without coupon or link.

Set "is_relevant": false only when the content is clearly organic:
- daily life update, scenery, family/personal content, joke/meme
- food/lifestyle with no product/brand/commercial intent
- vague mention with no product, brand, link, coupon, or recommendation

Default: if uncertain but there is commercial signal, mark relevant. If no commercial signal, mark organic.

==================================================
CONTENT TYPE PRIORITY
==================================================

Choose exactly one "content_type":
1. "coupon" — at least one valid coupon code exists
2. "collab_ad" — commercial link/affiliate/paid collab/sale campaign, no coupon
3. "recommendation" — product/brand recommendation without coupon/link
4. "organic" — not commercially relevant

Priority: coupon > collab_ad > recommendation > organic

==================================================
MULTIPLE COUPONS / MULTIPLE BRANDS
==================================================

A single story may contain multiple coupons, brands, or offers.
Extract EACH coupon/brand/offer as a separate object inside "coupon_items".
Do not merge separate coupons into one item.

==================================================
COUPON CODE EXTRACTION RULES
==================================================

A valid coupon code: 3–25 chars, letters/numbers/hyphen/underscore, not a full sentence, not only a percentage, phone number, price, or date.

Valid: ADI10, SIVAN20, SAVE-15, MORAN2025
Invalid: 20%, ₪50, 0501234567, 12.05.2026, קוד קופון

Never put percentages in "coupon_code". Put percentages in "discount_value".

If OCR gives uncertain text, preserve the likely code but lower confidence and add a note.
Example: OCR "ADl1O" probably means "ADI10" → coupon_code: "ADI10", confidence: 0.72, notes: "OCR may have confused I/1 or O/0"

==================================================
BRAND DETECTION
==================================================

Detect brand from text, hashtags, mentions, URL domain, logo, product packaging, context.
Do NOT use Linktree, Humanz, Instagram, bit.ly, taplink, or affiliate platforms as the brand.
Look for the real brand behind the link. If brand unknown, return "unknown". Do not invent brand names.

==================================================
DISCOUNT DETECTION
==================================================

discount_type: "percent" | "amount" | "free_shipping" | "gift" | "1_plus_1" | "sale" | "unknown" | null
Examples:
"20% הנחה" => discount_type: "percent", discount_value: 20
"50 ש״ח הנחה" => discount_type: "amount", discount_value: 50
"משלוח חינם" => discount_type: "free_shipping"
"1+1" => discount_type: "1_plus_1"

==================================================
VALIDITY / EXPIRATION
==================================================

Look for expiration hints: בתוקף עד, עד חצות, היום בלבד, 24 שעות, valid until, expires, today only.
Return validity_text for natural wording, expires_at (YYYY-MM-DD) if exact date known, null if unknown.
Do not invent expiration dates.

==================================================
CATEGORY CLASSIFICATION
==================================================

fashion | beauty | home | food | tech | kids | travel | health | education | other
Never return null category for relevant items.

==================================================
SOURCE PRIORITY
==================================================

1. Sticker/link text  2. OCR  3. Caption  4. Hashtags/mentions  5. URL/domain  6. Visual guess
Allowed source values: "ocr" | "caption" | "sticker" | "url" | "hashtag" | "mention" | "image" | "video" | "unknown"

==================================================
DESCRIPTION RULES
==================================================

Write a Hebrew marketing-style description. Max 200 chars. No emojis.
Good examples:
"קוד ADI10 מעניק 10% הנחה על מוצרי ביוטי של IL MAKIAGE"
"לינק רכישה לפריטי אופנה חדשים באתר TERMINAL X"

==================================================
DO NOT DO
==================================================

Do not: invent coupon codes, use percentages/phone numbers as coupon codes, use brand names as codes unless explicitly shown,
classify "קוד קופון" itself as the code, treat Linktree/Humanz/Bitly as the brand, merge multiple coupons,
output markdown, output explanations, return invalid JSON, leave mandatory fields missing.

==================================================
OUTPUT JSON SCHEMA
==================================================

{
  "is_relevant": true,
  "content_type": "coupon",
  "influencer_username": "string_or_null",
  "influencer_name": "string_or_null",
  "main_brand": "string_or_unknown",
  "main_coupon": "string_or_null",
  "main_url": "string_or_null",
  "category": "fashion|beauty|home|food|tech|kids|travel|health|education|other",
  "description_he": "string",
  "confidence": 0.0,
  "coupon_items": [
    {
      "brand": "string_or_unknown",
      "coupon_code": "string_or_null",
      "discount_type": "percent|amount|free_shipping|gift|1_plus_1|sale|unknown|null",
      "discount_value": "number_or_null",
      "currency": "ILS|USD|EUR|null",
      "product_name": "string_or_null",
      "product_category": "fashion|beauty|home|food|tech|kids|travel|health|education|other",
      "url": "string_or_null",
      "conditions": ["string"],
      "validity_text": "string_or_null",
      "expires_at": "YYYY-MM-DD_or_null",
      "source": "ocr|caption|sticker|url|hashtag|mention|image|video|unknown",
      "evidence_snippet": "short exact text that supports extraction",
      "confidence": 0.0,
      "notes": "string_or_null"
    }
  ],
  "urls": ["string"],
  "mentions": ["string"],
  "hashtags": ["string"],
  "raw_detected_codes": ["string"],
  "warnings": ["string"]
}

If not relevant, return:
{
  "is_relevant": false,
  "content_type": "organic",
  "influencer_username": null,
  "influencer_name": null,
  "main_brand": "unknown",
  "main_coupon": null,
  "main_url": null,
  "category": "other",
  "description_he": "תוכן אורגני ללא קופון, לינק מסחרי או המלצת מוצר ברורה",
  "confidence": 0.0,
  "coupon_items": [],
  "urls": [],
  "mentions": [],
  "hashtags": [],
  "raw_detected_codes": [],
  "warnings": []
}"""

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
URL_RE = re.compile(r"https?://[^\s)\]]+", re.IGNORECASE)

BAD_HOSTS = {
    "instagram.com", "www.instagram.com", "cdninstagram.com",
    "fbcdn.net", "fna.fbcdn.net", "scontent.cdninstagram.com",
}
BAD_IMAGE_HOSTS = BAD_HOSTS  # alias for image-specific checks


def extract_all_urls(row: Dict[str, Any]) -> List[str]:
    out = []
    for u in row.get("urls") or []:
        if isinstance(u, dict) and u.get("text"):
            out.append(u["text"])
        elif isinstance(u, str):
            out.append(u)
    for s in row.get("stickers") or []:
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            out += URL_RE.findall(t)
    for fld in ("caption_text", "ocr_text"):
        v = row.get(fld)
        if v:
            out += URL_RE.findall(v)
    for t in row.get("raw_text_candidates") or []:
        out += URL_RE.findall(str(t))
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
    return not any(h.endswith("." + bad) for bad in BAD_HOSTS)


def is_bad_image_host(url: str) -> bool:
    try:
        h = urlparse(url).netloc.lower()
        if h in BAD_IMAGE_HOSTS:
            return True
        return any(h.endswith("." + bh) for bh in BAD_IMAGE_HOSTS)
    except Exception:
        return True


def has_marketing_words(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    heb_terms = ["קופון", "קוד", "הנחה", "מבצע", "סייל", "לינק", "קישור",
                 "קנייה", "לרכישה", "קנו", "חדש", "הושק", "השקה", "חזר", "חזרה למלאי", "חזר למלאי"]
    eng_terms = ["coupon", "code", "promo", "discount", "sale", "shop", "buy",
                 "link", "new", "launch", "launched", "is back", "back in stock"]
    perc_or_price = any(sym in t for sym in ["%", "₪", "$", "€"])
    return perc_or_price or any(x in t for x in heb_terms + eng_terms)


def infer_category_from_text_or_image(row, result=None):  # noqa: ARG001 (result reserved for future image-based inference)
    text_fields = " ".join([
        str(row.get("caption_text") or ""), str(row.get("ocr_text") or ""),
        str(row.get("description") or ""), str(row.get("brand") or ""),
        " ".join(row.get("hashtags") or []),
    ]).lower()
    mapping = {
        "fashion": ["בגד", "בגדים", "ביגוד", "חולצה", "שמלה", "מכנס", "ג'ינס", "טרנינג",
                    "פיגמה", "פיג'מה", "הלבשה", "תחתונה", "אופנה", "reserved", "crazyline"],
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


def extract_and_validate_url_codes(urls: List[str], username: str = "") -> List[Dict[str, str]]:
    codes = []
    username_lower = username.lower()
    for url in urls:
        try:
            path = urlparse(url).path
            for part in re.split(r'[/_\-]', path):
                if not part or len(part) < 4:
                    continue
                part_upper = part.upper()
                if not is_valid_coupon_code(part_upper, username_lower):
                    continue
                is_influencer_code = username_lower and (
                    part.lower() in username_lower or username_lower in part.lower()
                )
                codes.append({
                    'code': part_upper, 'source': 'url_path', 'url': url,
                    'is_influencer_code': is_influencer_code, 'snippet': f"URL: {url[:100]}",
                })
        except Exception as e:
            logger.warning(f"Failed to extract code from URL {url}: {e}")
    seen = set()
    unique_codes = []
    for code_info in codes:
        if code_info['code'] not in seen:
            seen.add(code_info['code'])
            unique_codes.append(code_info)
    return unique_codes


def extract_pairs_from_stickers(row) -> list[dict]:
    out, texts = [], []
    username = (row.get("username") or "").lower()
    for s in row.get("stickers") or []:
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            texts.extend(re.split(r"[\r\n]+", t))
    for line in texts:
        line = line.strip()
        if not line or "instagram.com" in line.lower():
            continue
        parts = re.split(r"\s*[-–:]\s*", line, maxsplit=1)
        if len(parts) == 2:
            brand_he, right = parts
            brand_norm = next((v for k, v in BRAND_MAP_HE_IL.items() if k in brand_he), "unknown")
        else:
            right = line
            brand_norm = "unknown"
        for m in COUPON_RE.finditer(right):
            code = m.group(1).upper()
            if len(code) < 4 or code in IGNORE_COUPONS:
                continue
            if code.replace("-", "").isdigit() and len(code) > 6:
                continue
            if re.match(r'^\d{2}-\d{2}$', code):
                continue
            if re.match(r'^[0-9]+[A-Z]+[0-9]+[A-Z]+', code):
                continue
            if not re.search(r'[A-Z]', code):
                continue
            if sum(1 for c in code if c.isalpha()) < 2:
                continue
            if username and (code.lower() in username or username in code.lower()):
                continue
            out.append({"brand": brand_norm, "code": code, "source": "sticker", "snippet": line[:140]})
    return out


def brand_tokens_from_urls(urls: List[str]) -> List[str]:
    toks = []
    skip = {"instagram", "cdninstagram", "fbcdn", "fna", "com", "co", "il",
            "link", "bio", "shop", "store", "app", "api", "www"}
    skip_paths = {"posts", "stories", "reel", "p", "tv", "explore"}
    for u in urls:
        h = (host_of(u) or "").lower().replace("www.", "")
        for p in re.split(r"[\.\-]+", h):
            if len(p) >= 4 and p not in skip:
                toks.append(p)
        try:
            path = urlparse(u).path
            for p in re.split(r"[\/\-_]+", path):
                if p and len(p) >= 3 and not p.isdigit() and p.lower() not in skip_paths:
                    toks.append(p)
        except Exception:
            pass
    return sorted(set(toks))


# ============== NORMALIZE ROW ==============

def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    payload = r.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = None
    if isinstance(payload, dict):
        for k in (
            "type", "urls", "user_id", "hashtags", "mentions", "ocr_text", "stickers",
            "username", "image_url", "permalink", "video_url", "media_meta", "frames_used",
            "caption_text", "content_hash", "source_flags", "taken_at_iso", "language_guess",
            "ocr_confidence", "expiring_at_iso", "brand_candidates", "raw_text_candidates",
        ):
            if r.get(k) is None and payload.get(k) is not None:
                r[k] = payload.get(k)
    # extract user_id from media_id if missing
    if not r.get("user_id"):
        media_id = r.get("media_id", "")
        if "_" in media_id:
            parts = media_id.split("_")
            if len(parts) >= 2:
                r["user_id"] = parts[-1]
    return r


# ============== PRE-FILTER ==============

SIGNAL_KEYWORDS = {
    "קוד", "קופון", "הנחה", "הטבה", "מבצע", "סייל", "לרכישה", "קנייה", "תזינו", "בקופה",
    "code", "coupon", "promo", "discount", "sale", "voucher", "deal", "offer", "shop", "buy",
}


def has_signal(row: Dict[str, Any]) -> tuple[bool, str]:
    """Returns (signal_found, reason) so callers can log why the story was or wasn't sent to Gemini."""
    flags = row.get("source_flags") or {}
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except Exception:
            flags = {}
    if flags.get("has_text") or flags.get("has_stickers"):
        return True, "stickers_or_text_flag"

    urls = extract_all_urls(row)
    if any(is_brand_host(host_of(u)) for u in urls):
        return True, "brand_url"

    sticker_blob = " ".join(
        s.get("text", "") if isinstance(s, dict) else str(s)
        for s in (row.get("stickers") or [])
    )
    all_text = " ".join(filter(None, [
        row.get("caption_text") or "",
        row.get("ocr_text") or "",
        sticker_blob,
    ])).lower()

    if any(kw in all_text for kw in SIGNAL_KEYWORDS):
        return True, "keyword_in_text"

    # Visual-only stories (e.g. coupon list as a graphic image) have no extractable text.
    # Gemini already receives the image for analysis, so we let it decide.
    if row.get("image_url"):
        return True, "image_visual"

    return False, "no_signal"


# ============== GEMINI ==============

def fetch_image_as_data_uri(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        content = resp.content
        if len(content) > MAX_IMAGE_BYTES:
            print(f"⚠️ Image too big ({len(content)} bytes), skipping: {url[:100]}")
            return None
        ctype = resp.headers.get("Content-Type", "") or mimetypes.guess_type(url)[0] or "image/jpeg"
        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception as e:
        print(f"⚠️ Image fetch failed: {e}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((Timeout, RequestException)),
    reraise=True,
)
def call_gemini_with_retry(payload: Dict[str, Any]) -> requests.Response:
    logger.debug("Calling Gemini API...")
    response = requests.post(GEMINI_URL, headers=GEMINI_HEADERS, json=payload, timeout=90)
    if response.status_code >= 500:
        logger.warning(f"Gemini server error {response.status_code}, will retry...")
        response.raise_for_status()
    if response.status_code == 429:
        logger.warning("Rate limited, will retry...")
        response.raise_for_status()
    return response


def call_gemini_analyze(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    media_id = row.get("media_id")
    stickers_texts = []
    for s in row.get("stickers") or []:
        t = s.get("text") if isinstance(s, dict) else s
        if t:
            stickers_texts.append(str(t))

    user_blob = {
        "username": row.get("username"),
        "caption_text": row.get("caption_text"),
        "ocr_text": row.get("ocr_text"),
        "stickers": stickers_texts[:15],
        "urls": (row.get("urls") or [])[:12],
        "hashtags": (row.get("hashtags") or [])[:20],
        "mentions": (row.get("mentions") or [])[:10],
        "raw_text_candidates": (row.get("raw_text_candidates") or [])[:10],
    }

    content_parts = [{"type": "text", "text": json.dumps(user_blob, ensure_ascii=False)}]

    img_ref = None
    if row.get("image_url"):
        if is_bad_image_host(row["image_url"]):
            img_ref = fetch_image_as_data_uri(row["image_url"])
        else:
            img_ref = row["image_url"]
    if img_ref:
        content_parts.append({"type": "image_url", "image_url": {"url": img_ref, "detail": "low"}})

    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 5000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content_parts},
        ],
    }

    try:
        resp = call_gemini_with_retry(payload)

        if resp.status_code == 400 and img_ref and not img_ref.startswith("data:"):
            data_uri = fetch_image_as_data_uri(img_ref)
            if data_uri:
                content_parts[-1]["image_url"]["url"] = data_uri
                payload["messages"][1]["content"] = content_parts
                resp = call_gemini_with_retry(payload)

        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices") or []
            content = choices[0].get("message", {}).get("content") if choices else None
            if not content:
                logger.error(f"Null content from Gemini [{media_id}]: {str(data)[:400]}")
                return None
            # strip markdown code fences if model wraps response
            stripped = content.strip()
            if stripped.startswith("```"):
                stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
                stripped = re.sub(r"\s*```$", "", stripped)
            return json.loads(stripped)

        logger.error(f"❌ OpenAI error [{media_id}]: {resp.status_code} {resp.text[:300]}")
        return None
    except Exception as e:
        logger.error(f"❌ call_gemini_analyze failed [{media_id}]: {e}")
        return None


# ============== USERNAME MAP ==============

def load_id_to_username_map():
    try:
        files = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'user_ids_*.json'))
        if not files:
            print("⚠️ No user_ids_*.json files found for username mapping.")
            return {}
        latest_file = max(files, key=os.path.getctime)
        print(f"📂 Loading username mapping from: {latest_file}")
        with open(latest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        mapping = {}
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


# ============== PERFORMANCE REPORT ==============

def generate_performance_report():
    print("\n📊 מייצר דוח ביצועים...")
    try:
        id_map = load_id_to_username_map()
        print("   📥 שולף נתונים מהדאטה-בייס...")

        r = sb_get(f"{SUPABASE_REST}/{RAW_TABLE}", {"select": "user_id,media_id,processing", "limit": "10000"})
        all_stories = r.json() if r.status_code == 200 else []

        r = sb_get(f"{SUPABASE_REST}/{REL_TABLE}", {"select": "user_id,name,url,coupon", "limit": "10000"})
        relevant = r.json() if r.status_code == 200 else []

        r = sb_get(f"{SUPABASE_REST}/{REC_TABLE}", {"select": "user_id", "limit": "10000"})
        recommendations_rows = r.json() if r.status_code == 200 else []

        stats = defaultdict(lambda: {"total": 0, "coupons": 0, "links": 0, "recommendations": 0, "organic": 0})

        for story in all_stories:
            stats[str(story.get("user_id", "unknown"))]["total"] += 1

        for story in relevant:
            uid = str(story.get("user_id", "unknown"))
            if story.get("url"):
                stats[uid]["links"] += 1
            else:
                stats[uid]["coupons"] += 1

        for story in recommendations_rows:
            stats[str(story.get("user_id", "unknown"))]["recommendations"] += 1

        for story in all_stories:
            processing = story.get("processing") or {}
            if isinstance(processing, dict) and processing.get("status") == "non_relevant":
                stats[str(story.get("user_id", "unknown"))]["organic"] += 1

        report = {
            "timestamp": datetime.now().isoformat(),
            "total_influencers": len(stats),
            "total_stories": sum(s["total"] for s in stats.values()),
            "influencers": [],
        }

        for uid, data in stats.items():
            username = id_map.get(uid, f"user_{uid}")
            total = data["total"]
            relevant_count = data["coupons"] + data["links"] + data["recommendations"]
            score = int((relevant_count / total) * 100) if total > 0 else 0
            grade, emoji = (("A", "🟢") if score >= 70 else ("B", "🟡") if score >= 50
                            else ("C", "🟠") if score >= 30 else ("D", "🔴"))
            report["influencers"].append({
                "username": username, "user_id": uid, "score": score, "grade": grade, "emoji": emoji,
                "stats": {
                    "total_stories": total, "coupons": data["coupons"], "links": data["links"],
                    "recommendations": data["recommendations"], "organic": data["organic"],
                    "relevant_count": relevant_count,
                    "relevant_percentage": round((relevant_count / total * 100) if total > 0 else 0, 1),
                },
            })

        report["influencers"].sort(key=lambda x: x["score"], reverse=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"influencer_report_{timestamp}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"   ✅ דוח נשמר ב: {filename}")

        print("\n" + "=" * 100)
        print("📊 סיכום ביצועים")
        print("=" * 100)
        print(f"סה\"כ משפיעניות: {report['total_influencers']}")
        print(f"סה\"כ סטוריז: {report['total_stories']}")
        print(f"\nטופ 5 משפיעניות:")
        for inf in report["influencers"][:5]:
            s = inf["stats"]
            print(f"  {inf['emoji']} {inf['username']:<25} ציון: {inf['score']}/100 ({inf['grade']}) "
                  f"- {s['relevant_count']}/{s['total_stories']} רלוונטיים")
        if len(report["influencers"]) > 5:
            print(f"\n💡 לדוח מלא, פתחי את: {filename}")
        print("=" * 100 + "\n")

    except Exception as e:
        print(f"   ⚠️ שגיאה ביצירת דוח: {e}")


# ============== MAIN ==============

def main_v2():
    stats = RunStatistics()
    logger.info(f"Starting run_daily.py (DRY_RUN={DRY_RUN})")

    id_map = load_id_to_username_map()

    try:
        rows = [normalize_row(r) for r in get_raw_rows(MAX_ROWS)]
    except Exception as e:
        logger.error(f"❌ Failed to fetch rows: {e}")
        return

    rows_by_user: Dict[str, list] = {}
    for r in rows:
        raw_uid = r.get("user_id")
        if raw_uid is None or raw_uid == "":
            print(f"⚠️ WARNING: Story {r.get('media_id')} has no user_id!")
        uid = str(raw_uid or "unknown")
        rows_by_user.setdefault(uid, []).append(r)

    print(f"Fetched {len(rows)} rows, grouped into {len(rows_by_user)} users.")

    for uid, user_rows in rows_by_user.items():
        user_rows.sort(key=lambda x: x.get("taken_at_iso") or "")

        username = id_map.get(uid) or _safe_name(user_rows[0], {}) if user_rows else "unknown"
        print(f"\n👤 Processing User: {username} ({len(user_rows)} stories)")

        processed_ids = load_all_processed_ids(uid)
        print(f"   📋 Already processed: {len(processed_ids)} stories")

        session_dedup: set = set()

        pbar = tqdm(enumerate(user_rows, 1), total=len(user_rows), desc=f"Processing {username}",
                    unit="story", file=sys.stderr, leave=False)
        for idx, row in pbar:
            media_id = row.get("media_id") or f"row_{idx}"
            pbar.set_postfix({"story": media_id[:20]})

            row = normalize_row(row)
            uid = str(row.get("user_id") or "unknown")

            if uid in id_map:
                row["username"] = id_map[uid]
                if isinstance(row.get("payload"), dict):
                    row["payload"]["username"] = id_map[uid]

            if media_id in processed_ids:
                tqdm.write("    ↩️ Skipped (already processed)")
                continue

            # ── שלב 1: סינון Python (ללא AI) ──
            signal_found, signal_reason = has_signal(row)
            if not signal_found:
                tqdm.write("    ⚪ No signal — organic_no_signal, skipping AI")
                stats.add_story(username, "organic")
                set_processing_status(media_id, "organic_no_signal")
                continue
            if signal_reason == "image_visual":
                tqdm.write("    🖼️ Visual-only story — sending image to Gemini")

            # ── שלב 2: ניתוח AI ──
            ai_start = time.time()
            try:
                result = call_gemini_analyze(row)
                stats.add_processing_time(time.time() - ai_start)
                if not result:
                    result = {"is_relevant": False, "content_type": "organic", "confidence": 0.0}
            except Exception as e:
                tqdm.write(f"    ❌ OpenAI failed: {e}")
                set_processing_status(media_id, "error", str(e))
                stats.add_error()
                continue

            stats.add_cost(0.0003)
            tqdm.write(f"    🤖 AI: {result.get('content_type')} (Conf: {result.get('confidence', 0):.2f})")
            tqdm.write(f"    📝 {result.get('description_he', '')[:100]}")

            if result.get("is_relevant"):
                result = validate_ai_result(result, row)
                if not result.get("is_relevant"):
                    tqdm.write("ℹ️ Validation marked as not relevant")
                    stats.add_story(username, "organic")
                    set_processing_status(media_id, "non_relevant", None,
                                          {"ai_result": result, "reason": "validation_rejected"})
                    continue

                ctype = result.get("content_type")
                brand = (result.get("main_brand") or "unknown").strip().lower()

                dedup_token = None
                if ctype == "coupon":
                    dedup_token = (result.get("main_coupon") or "").strip().upper()
                elif ctype == "collab_ad":
                    u = result.get("main_url") or ""
                    try:
                        parsed = urlparse(u)
                        dedup_token = f"{parsed.netloc}{parsed.path}"
                    except Exception:
                        dedup_token = u
                elif ctype == "recommendation":
                    dedup_token = brand

                dedup_key = (ctype, brand, dedup_token)
                if dedup_key in session_dedup:
                    tqdm.write(f"    🔄 Duplicate in session: {dedup_key} - SKIPPING")
                    set_processing_status(media_id, "skipped", None, {"reason": "duplicate_in_session"})
                    continue
                session_dedup.add(dedup_key)

                tqdm.write(f"🚀 [{media_id}] {ctype} → saving...")
                stats.add_story(username, ctype)

                try:
                    if DRY_RUN:
                        tqdm.write(f"🔍 DRY RUN: Would insert {ctype} for {media_id}")
                    else:
                        inserted = upsert_relevant(row, result)
                        set_processing_status(media_id, "ok", None, {"decision": ctype})
                        if inserted is None:
                            tqdm.write("⏭️ Brand already in DB — skipped (not an error)")
                        else:
                            tqdm.write("✅ Inserted into relevant_story")
                except Exception as e:
                    tqdm.write(f"❌ Insert failed: {e}")
                    set_processing_status(media_id, "error", str(e))
            else:
                tqdm.write("ℹ️ Not relevant (Organic)")
                stats.add_story(username, "organic")
                set_processing_status(media_id, "non_relevant", None, {"ai_result": result})

        pbar.close()

    stats.print_summary()


if __name__ == "__main__":
    main_v2()
