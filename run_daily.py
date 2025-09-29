import os
import time
import json
import re
import requests
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from requests.exceptions import RequestException, Timeout
from urllib.parse import urlparse
import base64
import re, json, time, base64, mimetypes
from io import BytesIO
from urllib.parse import urlparse
import requests
from requests.exceptions import Timeout, RequestException
from datetime import datetime, timezone
try:
    from PIL import Image
    PIL_OK = True
except Exception:
    Image = None
    PIL_OK = False

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB – תשאירי מרווח סביר

def fetch_image_as_data_url(url: str) -> Optional[str]:
    """מוריד תמונה ומחזיר data:image/...;base64,<...> לשימוש עם Vision.
       מחזיר None אם נכשל (ואז נדלג על צילום/נשתמש ב-URL בלבד)."""
    try:
        r = requests.get(
            url,
            timeout=25,
            headers={
                # חלק מה-CDNs רגישים ל-UA/Referer
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        r.raise_for_status()
        b = r.content
        if not b:
            return None
        if len(b) > MAX_IMAGE_BYTES:
            # אפשר לצמצם/לדחוס – בשלב ראשון רק נוותר
            dlog("Image too large for data URL", level="WARN", obj={"bytes": len(b), "url": url})
            return None
        mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0] or "image/jpeg"
        b64 = base64.b64encode(b).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        dlog("fetch_image_as_data_url failed", level="WARN", obj={"err": str(e), "url": url})
        return None

def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    פותחת את ה-payload (אם קיים) ומשלבת שדות חסרים (image_url, urls, stickers, וכו') לתוך row.
    אם payload הוא מחרוזת JSON – נמיר לדיקט.
    עדיפות: לא נדרוס שדות שכבר קיימים ב-row ברמת הטופ.
    """
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = None

    if isinstance(payload, dict):
        for k, v in payload.items():
            if k not in row or row[k] in (None, "", [], {}):
                row[k] = v

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


SUPABASE_URL = "https://dgxkdenkbaphzabkcybq.supabase.co".rstrip("/")
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRneGtkZW5rYmFwaHphYmtjeWJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTAxMTA2OCwiZXhwIjoyMDc0NTg3MDY4fQ.A2UCwyK2fVYTv6JUwPqv5sSoz9XvtErNcCn2B55hquk"  # 🔒 החלף ב-SERVICE_ROLE שלך

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "YOUR_OPENAI_API_KEY"
if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY")

RAW_TABLE = "stories_raw"
REL_TABLE = "relevant_story"
MODEL = "gpt-4.1-mini"
MAX_ROWS = 50

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
# ====================================

LAST_CALL_TS = 0.0
MIN_INTERVAL_S = 0.5
def _safe_name(row: Dict[str, Any], result: Dict[str, Any]) -> str:
    """
    מחזירה תמיד name לא-ריק:
    1) מהמודל אם קיים
    2) מהשורה ברמות שונות
    3) fallback ל-user_id
    4) ואם אין – "unknown"
    """
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    candidates = [
        result.get("name"),
        row.get("name"),
        row.get("username"),
        payload.get("name"),
        payload.get("username"),
        payload.get("owner", {}).get("username") if isinstance(payload.get("owner"), dict) else None,
        str(row.get("user_id") or "").strip(),
        "unknown",
    ]
    for c in candidates:
        if c and str(c).strip():
            return str(c).strip()
    return "unknown"

def rate_limit_sleep():
    global LAST_CALL_TS
    now = time.time()
    delta = now - LAST_CALL_TS
    if delta < MIN_INTERVAL_S:
        time.sleep(MIN_INTERVAL_S - delta)
    LAST_CALL_TS = time.time()

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sb_get(path: str, params: Dict[str, str]) -> requests.Response:
    return requests.get(path, headers=SB_HEADERS, params=params, timeout=30)

def sb_patch(path: str, params: Dict[str, str], body: Dict[str, Any]) -> requests.Response:
    return requests.patch(path, headers=SB_HEADERS, params=params, data=json.dumps(body), timeout=30)

def sb_post(path: str, body: Dict[str, Any], prefer: Optional[str] = None, params: Optional[Dict[str, str]] = None) -> requests.Response:
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
    }
    r = sb_get(url, params)
    if r.status_code >= 400:
        print("❌ Supabase error (raw fetch):", r.status_code, r.text)
    r.raise_for_status()
    return r.json()

def set_processing_status(media_id: str, status: str, last_error: Optional[str] = None, extra: Optional[Dict[str, Any]] = None):
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {"media_id": f"eq.{media_id}"}
    payload = {
        "processing": {
            "status": status,
            "last_error": last_error,
            "ts": now_iso(),
            **(extra or {})
        }
    }
    r = sb_patch(url, params, payload)
    if r.status_code >= 400:
        print("⚠️ set_processing_status failed:", r.status_code, r.text)

def already_in_relevant(media_id: str) -> bool:
    url = f"{SUPABASE_REST}/{REL_TABLE}"
    params = {"select": "media_id", "media_id": f"eq.{media_id}", "limit": "1"}
    r = sb_get(url, params)
    if r.status_code >= 400:
        print("⚠️ relevant lookup error:", r.status_code, r.text)
        return False
    return bool(r.json())

def upsert_relevant(row: Dict[str, Any], result: Dict[str, Any]):
    payload = {
        "media_id": row.get("media_id"),
        "user_id": row.get("user_id"),
        "name": _safe_name(row, result),     # <<<<<<<<<<<<<<<<<<<<<< חשוב
        "brand": result.get("brand"),
        "coupon": result.get("coupon"),
        "url": result.get("url"),
        "date": row.get("taken_at_iso"),
        "Description": (
            (result.get("Description") or "").strip()
            or (row.get("caption_text") or "").strip()
            or (row.get("ocr_text") or "").strip()
            or None
        ),
    }
    url = f"{SUPABASE_REST}/{REL_TABLE}"
    r = sb_post(
        url, payload,
        prefer="return=representation,resolution=merge-duplicates",
        params={"on_conflict": "media_id"}
    )
    if r.status_code >= 400:
        print("❌ upsert relevant failed:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()

#############################################################
SYSTEM_PROMPT = r"""You are filter_json_bot.
Goal: Decide if a single Instagram media row is relevant and extract structured fields for database insertion.

Two relevance modes (return TRUE if either holds):
1) STRICT: has_collab AND (has_coupon_code OR has_url)

2) RELAXED-A: brand_url_present AND (brand_token_present OR marketing_intent OR price_percent_token)
   - brand_token_present: brand/domain tokens (from any URL host, e.g. "chanivainberger", "vainberger") appear in stickers/ocr/caption/hashtags.
   - marketing_intent: words like קופון, קוד, הנחה, מבצע, סייל, חדש, הושק, חזר/חזרה למלאי, לינק, קנייה/לרכישה, buy, shop, sale, discount, code, promo, link.
   - price_percent_token: any currency (₪, $, €) or a percentage like 10%.

Explicit collab signals (case-insensitive):
Hebrew: "בשיתוף פעולה","שת״פ","שתפ","תוכן ממומן","פרסומת","חסות","בשיתוף עם","פרסומי"
English: "paid partnership","sponsored","ad","#ad","#sponsored","#paidpartnership","partnered","collab"
Hashtags: #ad #ads #sponsored #partner #paidpartnership #שת״פ #שתפ #בשיתוף_פעולה

Coupon patterns:
1) /(קופון|coupon|promo|voucher)\s*[:：]?\s*([A-Za-z0-9_-]{3,20})/i
2) /\b([A-Z0-9]{4,10})\b(?:\s*(code|קוד))?/i

URL rules:
- has_url: any valid URL or permalink.
- brand_url_present: TRUE if at least one URL host is NOT instagram/cdninstagram/fbcdn/fna.fbcdn.

Brand:
- Prefer brand token on-image; else use brand URL host (without 'www.'); else null.

Name:
- Influencer display name if given; else username; else null.

Description:
- Concise (<=240 chars), prefer Hebrew if source is Hebrew.

Evidence arrays:
- Include exact snippets (from text/stickers/hashtags/on-image text/URL list).

Input you get (JSON text + one image_url):
- caption_text, ocr_text, stickers texts, hashtags, urls (list of strings), username,
  has_image, has_video, permalink_present, image_url.

Output (STRICT JSON only):
{
  "is_relevant": boolean,
  "brand": string|null,
  "name": string|null,
  "coupon": string|null,
  "url": string|null,
  "Description": string|null,
  "evidence": {
    "collab": [string],
    "coupon": [string],
    "url": [string]
  }
}
Return ONLY valid JSON. No commentary.
"""


def minimal_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "caption_text": row.get("caption_text"),
        "ocr_text": row.get("ocr_text"),
        "stickers": row.get("stickers"),
        "hashtags": row.get("hashtags"),
        "urls": row.get("urls"),            # ← חדש
        "username": row.get("username"),    # ← חדש
        "has_image": bool(row.get("image_url")),
        "has_video": bool(row.get("video_url")),
        "permalink_present": bool(row.get("permalink")),
        "image_url": row.get("image_url"),  # ← נוח גם כאן
    }


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    m = JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception as e:
        print("❌ Failed to extract JSON:", e)
        return None
URL_RE = re.compile(r'https?://[^\s)\]]+', re.IGNORECASE)

BAD_HOSTS = {
    "instagram.com", "www.instagram.com",
    "cdninstagram.com", "fbcdn.net", "fna.fbcdn.net", "scontent.cdninstagram.com"
}

def extract_all_urls(row: Dict[str, Any]) -> List[str]:
    out = []

    # 1) urls[]
    for u in (row.get("urls") or []):
        if isinstance(u, dict) and u.get("text"):
            out.append(u["text"])
        elif isinstance(u, str):
            out.append(u)

    # 2) stickers[].text
    for s in (row.get("stickers") or []):
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            out += URL_RE.findall(t)

    # 3) caption/ocr/raw_text_candidates
    for fld in ("caption_text", "ocr_text"):
        v = row.get(fld)
        if v:
            out += URL_RE.findall(v)
    for t in (row.get("raw_text_candidates") or []):
        out += URL_RE.findall(str(t))

    # ייחוד וניקוי
    seen = set(); clean=[]
    for u in out:
        u = u.strip().strip('.,);]')
        if u and u not in seen:
            seen.add(u); clean.append(u)
    return clean

def host_of(u: str) -> Optional[str]:
    try:
        return urlparse(u).hostname
    except Exception:
        return None

def is_brand_host(host: Optional[str]) -> bool:
    if not host: return False
    h = host.lower()
    if h in BAD_HOSTS: return False
    # תזרקי גם תתי־דומיינים של האזורים האסורים
    for bad in BAD_HOSTS:
        if h.endswith("."+bad):
            return False
    return True

def has_marketing_words(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    heb_terms = [
        "קופון","קוד","הנחה","מבצע","סייל","לינק","קישור",
        "קנייה","לרכישה","קנו","חדש","הושק","השקה","חזר","חזרה למלאי","חזר למלאי"
    ]
    eng_terms = [
        "coupon","code","promo","discount","sale","shop","buy","link",
        "new","launch","launched","is back","back in stock"
    ]
    perc_or_price = any(sym in t for sym in ["%", "₪", "$", "€"])
    return perc_or_price or any(x in t for x in heb_terms+eng_terms)

def call_openai_filter(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Core vision+text decision:
    - Builds robust user payload (caption/ocr/stickers/hashtags/urls + hints).
    - Adds the image (with CDN-safe fallback to data: URI).
    - Uses STRICT or RELAXED criteria (described in SYSTEM_PROMPT on the server side).
    - Returns a strict-JSON dict or None on failure.
    """

    # ---------- local logging (uses global dlog() if exists) ----------
    def _dlog(*args, level="INFO", obj=None):
        try:
            if 'dlog' in globals() and callable(globals()['dlog']):
                globals()['dlog'](*args, level=level, obj=obj)
                return
        except Exception:
            pass
        # fallback minimal logger
        ts = datetime.now().strftime("%H:%M:%S")
        msg = " ".join(str(a) for a in args)
        print(f"[{ts}] [{level}] {msg}")
        if obj is not None:
            try:
                print(json.dumps(obj, ensure_ascii=False)[:1200])
            except Exception:
                print(str(obj)[:1200])

    # ---------- small utils (self-contained) ----------
    URL_RE = re.compile(r'https?://[^\s)\]]+', re.IGNORECASE)
    BAD_IMAGE_HOSTS = {
        "instagram.com", "www.instagram.com",
        "cdninstagram.com", "scontent.cdninstagram.com",
        "fbcdn.net", "fna.fbcdn.net"
    }
    BAD_URL_HOSTS = {
        "instagram.com", "www.instagram.com",
        "cdninstagram.com", "fbcdn.net", "fna.fbcdn.net", "scontent.cdninstagram.com"
    }

    def _short(s, n=420):
        if s is None:
            return ""
        try:
            s = re.sub(r"\s+", " ", str(s))
        except Exception:
            s = str(s)
        return s[:n]

    def host_of(u: str) -> Optional[str]:
        try:
            return urlparse(u).hostname
        except Exception:
            return None

    def is_bad_image_host(u: str) -> bool:
        try:
            h = (urlparse(u).hostname or "").lower()
            if h in BAD_IMAGE_HOSTS:
                return True
            return any(h.endswith("."+bh) for bh in BAD_IMAGE_HOSTS)
        except Exception:
            return True

    def is_brand_host(host: Optional[str]) -> bool:
        if not host:
            return False
        h = host.lower()
        if h in BAD_URL_HOSTS:
            return False
        for bad in BAD_URL_HOSTS:
            if h.endswith("."+bad):
                return False
        return True

    def brand_tokens_from_urls(urls: List[str]) -> List[str]:
        toks = []
        for u in urls:
            h = (host_of(u) or "").lower().replace("www.", "")
            parts = re.split(r"[\.\-]+", h)
            for p in parts:
                if len(p) >= 4 and p not in ("instagram","cdninstagram","fbcdn","fna","com","co","il"):
                    toks.append(p)
        return sorted(set(toks))

    def has_marketing_words(text: str) -> bool:
        if not text:
            return False
        t = text.lower()
        heb_terms = [
            "קופון","קוד","הנחה","מבצע","סייל","לינק","קישור",
            "קנייה","לרכישה","קנו","חדש","הושק","השקה","חזר","חזרה למלאי","חזר למלאי"
        ]
        eng_terms = [
            "coupon","code","promo","discount","sale","shop","buy","link",
            "new","launch","launched","is back","back in stock"
        ]
        perc_or_price = any(sym in t for sym in ["%", "₪", "$", "€"])
        return perc_or_price or any(x in t for x in heb_terms+eng_terms)

    def extract_all_urls(row_: Dict[str, Any]) -> List[str]:
        out = []
        # 1) urls[]
        for u in (row_.get("urls") or []):
            if isinstance(u, dict) and u.get("text"):
                out.append(u["text"])
            elif isinstance(u, str):
                out.append(u)
        # 2) stickers[].text
        for s in (row_.get("stickers") or []):
            t = s.get("text") if isinstance(s, dict) else None
            if t:
                out += URL_RE.findall(t)
        # 3) caption/ocr/raw_text_candidates
        for fld in ("caption_text", "ocr_text"):
            v = row_.get(fld)
            if v:
                out += URL_RE.findall(v)
        for t in (row_.get("raw_text_candidates") or []):
            out += URL_RE.findall(str(t))
        # unique & trim
        seen, clean = set(), []
        for u in out:
            u2 = u.strip().strip('.,);]')
            if u2 and u2 not in seen:
                seen.add(u2)
                clean.append(u2)
        return clean

    def fetch_image_as_data_uri(url: str, max_bytes: int = 4_000_000) -> Optional[str]:
        try:
            r = requests.get(
                url, timeout=25,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": "https://www.instagram.com/",
                },
            )
            if r.status_code != 200 or not r.content:
                return None
            content = r.content
            ctype = r.headers.get("Content-Type") or mimetypes.guess_type(url)[0] or "image/jpeg"

            # compress if too large and PIL available
            if len(content) > max_bytes:
                try:
                    if 'PIL_OK' in globals() and globals().get('PIL_OK') and 'Image' in globals():
                        im = globals()['Image'].open(BytesIO(content)).convert("RGB")
                        buf = BytesIO()
                        im.save(buf, format="JPEG", quality=70, optimize=True)
                        content = buf.getvalue()
                        ctype = "image/jpeg"
                    # else: keep as is (may be big)
                except Exception:
                    pass

            b64 = base64.b64encode(content).decode("ascii")
            return f"data:{ctype};base64,{b64}"
        except Exception as e:
            _dlog("fetch_image_as_data_uri failed", level="WARN", obj={"err": str(e), "url": url})
            return None

    # ---------- normalize & introspect ----------
    media_id = row.get("media_id")
    _dlog("➡️ call_openai_filter for media_id:", media_id)

    # URLs & brand signals
    urls_all = extract_all_urls(row)
    _dlog("URLs collected:", obj=urls_all)

    brand_urls = [u for u in urls_all if is_brand_host(host_of(u))]
    _dlog("Brand URLs:", obj=brand_urls)

    brand_tokens = brand_tokens_from_urls(brand_urls)
    _dlog("Brand tokens:", obj=brand_tokens)

    # stickers summarized
    stickers_texts = []
    for s in (row.get("stickers") or []):
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            stickers_texts.append(_short(t, 140))

    hashtags = [
        ("#"+h) if not str(h).startswith("#") else str(h)
        for h in (row.get("hashtags") or [])
    ]

    # marketing intent from all visible text
    all_text = " ".join([
        str(row.get("caption_text") or ""),
        str(row.get("ocr_text") or ""),
        " ".join(stickers_texts)
    ])
    hint_marketing_intent = has_marketing_words(all_text)
    _dlog("Marketing intent?", hint_marketing_intent)

    # ---------- build user payload ----------
    user_blob = {
        "username": row.get("username"),
        "caption_text": _short(row.get("caption_text")),
        "ocr_text": _short(row.get("ocr_text")),
        "stickers_texts": stickers_texts[:10],
        "hashtags": hashtags[:12],
        "urls": urls_all[:12],

        "brand_url_present": bool(brand_urls),
        "brand_urls": brand_urls[:8],
        "brand_tokens": brand_tokens[:8],

        "hints": {
            "brand_url_present": bool(brand_urls),
            "marketing_intent": hint_marketing_intent
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
                _dlog("Failed to build data URI, proceeding without image", level="WARN")
        else:
            img_ref = row["image_url"]

    # ---------- messages ----------
    content_parts = [{"type": "text", "text": json.dumps(user_blob, ensure_ascii=False)}]
    if img_ref:
        content_parts.append({"type": "image_url", "image_url": {"url": img_ref, "detail": "low"}})

    payload = {
        "model": MODEL,                 # must be a Vision model (e.g., "gpt-4.1-mini")
        "temperature": 0,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content_parts},
        ],
        "response_format": {"type": "json_object"}
    }
    _dlog("OpenAI payload (short): model=", MODEL)

    # ---------- send with retries ----------
    for attempt in range(1, 6):
        try:
            if 'rate_limit_sleep' in globals() and callable(globals()['rate_limit_sleep']):
                globals()['rate_limit_sleep']()
            print("=" * 50)
            print(f"▶️ Attempt {attempt}")
            resp = requests.post(OPENAI_URL, headers=OA_HEADERS, json=payload, timeout=90)
            print("⬅️ Status:", resp.status_code)
        except Timeout:
            _dlog("⏱️ Timeout. Retrying...", level="WARN")
            time.sleep(2)
            continue
        except RequestException as e:
            _dlog("❌ Network error:", level="ERROR", obj=str(e))
            return None

        body_text = resp.text
        _dlog("OpenAI raw response (short):", obj=_short(body_text, 1200))

        if resp.status_code == 200:
            try:
                content = resp.json()["choices"][0]["message"]["content"]
                _dlog("Model content (short):", obj=_short(content, 800))
                return json.loads(content)
            except Exception:
                # fallback naive JSON extraction
                try:
                    start = body_text.index("{"); end = body_text.rindex("}") + 1
                    return json.loads(body_text[start:end])
                except Exception as e:
                    _dlog("JSON parse failed (even fallback).", level="ERROR", obj=str(e))
                    return None

        if resp.status_code == 429:
            _dlog("⚠️ 429 - Sleeping 5s", level="WARN"); time.sleep(5); continue
        if resp.status_code in (500, 502, 503, 504):
            _dlog("⚠️ Server error - Sleeping 3s", level="WARN"); time.sleep(3); continue

        # 400 invalid_image_url => try once to force data URI if not done
        if resp.status_code == 400 and img_ref and isinstance(img_ref, str) and not img_ref.startswith("data:"):
            _dlog("400 invalid_image_url; forcing data URI fallback", level="WARN")
            data_uri = fetch_image_as_data_uri(img_ref)
            if data_uri:
                content_parts[-1]["image_url"]["url"] = data_uri
                payload["messages"][1]["content"] = content_parts
                continue

        _dlog("❌ OpenAI error:", level="ERROR", obj={"status": resp.status_code, "body": _short(body_text, 800)})
        return None

    raise RuntimeError("Request failed after retries")

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
        for k in ("type","urls","user_id","hashtags","mentions","ocr_text","stickers",
                  "username","image_url","permalink","video_url","media_meta",
                  "frames_used","caption_text","content_hash","source_flags",
                  "taken_at_iso","language_guess","ocr_confidence","expiring_at_iso",
                  "brand_candidates","raw_text_candidates"):
            if r.get(k) is None and payload.get(k) is not None:
                r[k] = payload.get(k)
    return r
BAD_IMAGE_HOSTS = {
    "instagram.com","www.instagram.com",
    "cdninstagram.com","scontent.cdninstagram.com",
    "fbcdn.net","fna.fbcdn.net"
}

def is_bad_image_host(u: str) -> bool:
    try:
        h = (urlparse(u).hostname or "").lower()
        if h in BAD_IMAGE_HOSTS: 
            return True
        return any(h.endswith("."+bh) for bh in BAD_IMAGE_HOSTS)
    except Exception:
        return True

def fetch_image_as_data_uri(url: str, max_bytes: int = 4_000_000) -> Optional[str]:
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.content:
            return None
        content = r.content
        ctype = r.headers.get("Content-Type") or mimetypes.guess_type(url)[0] or "image/jpeg"

        # אם גדול מדי – נכווץ רק אם PIL זמין; אחרת נשלח כמו שהוא
        if len(content) > max_bytes and PIL_OK:
            try:
                im = Image.open(BytesIO(content)).convert("RGB")
                buf = BytesIO()
                # כיווץ ל־JPEG באיכות 70 (תוכלי להעלות/להוריד)
                im.save(buf, format="JPEG", quality=70, optimize=True)
                content = buf.getvalue()
                ctype = "image/jpeg"
            except Exception:
                pass

        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception:
        return None

def _short(s, n=1000):
    try:
        s = str(s)
        s = re.sub(r"\s+", " ", s)
        return s[:n]
    except Exception:
        return s

def should_process(row: Dict[str, Any]) -> bool:
    return bool(row.get("image_url") or row.get("video_url"))
def brand_tokens_from_urls(urls: List[str]) -> List[str]:
    toks = []
    for u in urls:
        h = host_of(u) or ""
        h = h.lower().replace("www.", "")
        # דוגמה פשוטה: הפרדה לפי נקודות ומקפים
        parts = re.split(r"[\.\-]+", h)
        for p in parts:
            if len(p) >= 4 and p not in ("instagram","cdninstagram","fbcdn","fna","com","co","il"):
                toks.append(p)
    return sorted(set(toks))

def main():
    try:
        rows = get_raw_rows(MAX_ROWS)
    except Exception as e:
        print("❌ Failed to fetch rows:", e)
        return
    dlog("SB auth header startswith 'eyJ'? (anon-ish):", 
     obj=str(SB_HEADERS.get("Authorization",""))[:10])

    print(f"Fetched {len(rows)} rows from {RAW_TABLE}")

    for idx, row in enumerate(rows, 1):
        media_id = row.get("media_id") or f"row_{idx}"
        print(f"\n[{idx}/{len(rows)}] Processing: {media_id}")
                # פריסת payload -> תמלא image_url/urls/stickers וכו' אם חסר
        row = normalize_row(row)
        dlog("After normalize_row keys:", obj=list(row.keys()))
        dlog("Quick check fields:", obj={
            "image_url": row.get("image_url"),
            "urls": row.get("urls"),
            "stickers_len": len(row.get("stickers") or []),
            "caption_text": _short(row.get("caption_text")),
        })
        
        try:
            if already_in_relevant(media_id):
                print("↩️  Skipped (already in relevant_story)")
                set_processing_status(media_id, "skipped", None, {"reason": "already_in_relevant"})
                continue
        except Exception as e:
            print("⚠️ relevant existence check failed:", e)

        #if not should_process(row):
           # print("↩️  Skipped (no image or video present)")
            #set_processing_status(media_id, "skipped", None, {"reason": "no_image_or_video"})
            #continue

        try:
            result = call_openai_filter(row)
        except Exception as e:
            print("❌ OpenAI call failed:", e)
            set_processing_status(media_id, "error", str(e))
            continue

        if not result:
            print("↩️  No result from model")
            set_processing_status(media_id, "error", "no_result_from_model")
            continue

        if result.get("is_relevant"):
            try:
                upsert_relevant(row, result)
                set_processing_status(media_id, "ok", None, {"decision": "relevant"})
                print("✅ Inserted into relevant_story")
            except Exception as e:
                print("❌ Insert/Upsert failed:", e)
                set_processing_status(media_id, "error", str(e))
        else:
            print("ℹ️ Not relevant")
            set_processing_status(media_id, "non_relevant", None)

if __name__ == "__main__":
    main()
