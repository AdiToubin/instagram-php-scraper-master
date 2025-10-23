import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
import re, json, time, base64, mimetypes
from io import BytesIO
import requests
from requests.exceptions import Timeout, RequestException
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRneGtkZW5rYmFwaHphYmtjeWJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTAxMTA2OCwiZXhwIjoyMDc0NTg3MDY4fQ.A2UCwyK2fVYTv6JUwPqv5sSoz9XvtErNcCn2B55hquk"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "YOUR_OPENAI_API_KEY"
if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY")

RAW_TABLE = "stories_raw"
REL_TABLE = "relevant_story"
MODEL = "gpt-4.1-mini"
MAX_ROWS = 1000

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
        (
            payload.get("owner", {}).get("username")
            if isinstance(payload.get("owner"), dict)
            else None
        ),
        str(row.get("user_id") or "").strip(),
        "unknown",
    ]
    for c in candidates:
        if c and str(c).strip():
            return str(c).strip()
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
COUPON_RE = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9_-]{3,19})\b")


def extract_pairs_from_stickers(row) -> list[dict]:
    out, texts = [], []
    for s in row.get("stickers") or []:
        t = s.get("text") if isinstance(s, dict) else None
        if t:
            texts.extend(re.split(r"[\r\n]+", t))
    for line in texts:
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"\s*[-–:]\s*", line, maxsplit=1)
        if len(parts) != 2:
            continue
        brand_he, right = parts
        brand_norm = next(
            (v for k, v in BRAND_MAP_HE_IL.items() if k in brand_he), "unknown"
        )
        for m in COUPON_RE.finditer(right):
            code = m.group(1).upper()
            if len(code) < 4:
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


def already_in_relevant(media_id: str) -> bool:
    url = f"{SUPABASE_REST}/{REL_TABLE}"
    params = {"select": "media_id", "media_id": f"eq.{media_id}", "limit": "1"}
    r = sb_get(url, params)
    if r.status_code >= 400:
        print("⚠️ relevant lookup error:", r.status_code, r.text)
        return False
    return bool(r.json())


def upsert_relevant(row: Dict[str, Any], result: Dict[str, Any]):
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
        "media_id": row.get("media_id"),
        "user_id": row.get("user_id"),
        "name": _safe_name(row, result),
        "brand": _safe_brand(row, result),  # לעמוד ב-NOT NULL הקיים
        "coupon": result.get("coupon"),
        "url": result.get("url"),
        "date": row.get("taken_at_iso"),
        "Description": (
            (result.get("Description") or "").strip()
            or (row.get("caption_text") or "").strip()
            or (row.get("ocr_text") or "").strip()
            or None
        ),
        # חדש: לשמור את כל הרשימות
        "coupons": list(sorted({c.upper() for c in (result.get("coupons") or [])})),
        "coupon_items": dedup,
    }

    url = f"{SUPABASE_REST}/{REL_TABLE}"
    r = sb_post(
        url,
        payload,
        prefer="return=representation,resolution=merge-duplicates",
        params={"on_conflict": "media_id"},
    )
    if r.status_code >= 400:
        print("❌ upsert relevant failed:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


#############################################################
SYSTEM_PROMPT = r"""
You are an expert Instagram media analyzer.  
You receive text + optional image and must output a single valid JSON object
representing a commercial Instagram post.

Your goal is to **accurately detect real coupon codes, real brand names, and real store URLs**.
Avoid false positives such as tracking parameters or system words.

========================
WHAT TO DETECT
========================
1. **Coupon codes**
   - A valid coupon is a short token (3–15 chars) of letters/numbers/hyphens/underscores.
   - Usually appears near keywords like: 
     קוד, קופון, coupon, code, promo, discount, מבצע, הנחה.
   - Also appears alone in uppercase (e.g., SIVAN10, PROHAIRPLUS).
   - **Ignore any of the following technical words:**
     UTM_SOURCE, UTM_MEDIUM, UTM_CAMPAIGN, UTM_CONTENT, UTM_TERM, FBCLID, COLLECTIONS,
     SOCIAL, INSTAGRAM, PAGE, PRODUCT, CATEGORY, ITEM, LINK, HTTPS, HTTP, WWW, COM, IL.
   - From all candidates, keep only those that look like a human coupon (brand-related, short, meaningful).
   - Prefer codes with letters + digits (e.g., "CHANI10", "SIVAN15").
   - The main code goes in "coupon"; all unique ones go in "coupons" array.

2. **Brand detection**
   - Use the external URL domain or visible text/logo (e.g., prohairplus.co.il → brand="prohairplus").
   - If brand is written in Hebrew, map to Latin form:
     קולנטה→kolenta, מילא→mila, נעמה→naama, פלטין אקספרס→platinexpress,
     חני וינברגר→chanivainberger, מהדרין אונליין→mehadrinonline,
     ריזרבד→reserved, הום סטייל→homestyle.
   - If brand name appears in coupon (e.g., "PROHAIRPLUS") – assign it to that brand.

3. **External URLs**
   - Only keep non-Instagram, non-Facebook links.
   - Example: https://www.prohairplus.co.il/... is valid.
   - Do not extract UTM parameters as separate coupons.

4. **Relevance**
   - "is_relevant"=true only if at least one of:
       • coupon code found
       • explicit marketing intent (מבצע, קוד קופון, הנחה)
       • external store URL with product or collection
       • collaboration marker (#ad, sponsored, בשיתוף)
   - Be conservative: if not sure — set is_relevant=false.

5. **Description**
   - Create a short factual summary (≤200 chars, Hebrew preferred).
   - Example: "קופון הנחה למוצרי PROHAIRPLUS לשיער מתולתל".

========================
OUTPUT JSON SCHEMA
========================
{
  "is_relevant": boolean,
  "content_type": "coupon" | "collab_ad" | "recommendation" | "organic",
  "brand": string | null,
  "coupon": string | null,
  "coupons": string[],
  "url": string | null,
  "urls": string[],
  "Description": string | null,
  "category": "fashion" | "beauty" | "home" | "food" | "tech" | "other",
  "coupon_items": [
    {
      "brand": string | null,
      "code": string,
      "source": "caption|hashtag|ocr|sticker|image|url",
      "snippet": string
    }
  ]
}

========================
CATEGORY RULES
========================
- fashion → clothes, style, boutique, חולצה, חצאית, CRAZYLINE
- beauty → hair, shampoo, cream, טיפוח, קרם, PROHAIRPLUS
- food → recipe, בישול, מתכון, שוקולד
- home → בית, רהיט, מטבח, הום סטייל
- tech → גאדג'ט, אתר, אפליקציה
- other → anything else

========================
RULES
========================
- Return ONLY valid JSON (no commentary).
- Never invent coupons or brands.
- Ignore UTM and social tracking parameters.
- Prefer Hebrew in Description.
- Be deterministic and consistent.
"""


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
                print(json.dumps(obj, ensure_ascii=False)[:1200])
            except Exception:
                print(str(obj)[:1200])

    # ---------- small utils (self-contained) ----------
    URL_RE = re.compile(r"https?://[^\s)\]]+", re.IGNORECASE)
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
            return any(h.endswith("." + bh) for bh in BAD_IMAGE_HOSTS)
        except Exception:
            return True

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
            h = (host_of(u) or "").lower().replace("www.", "")
            parts = re.split(r"[\.\-]+", h)
            for p in parts:
                if len(p) >= 4 and p not in (
                    "instagram",
                    "cdninstagram",
                    "fbcdn",
                    "fna",
                    "com",
                    "co",
                    "il",
                ):
                    toks.append(p)
        return sorted(set(toks))

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

    def extract_all_urls(row_: Dict[str, Any]) -> List[str]:
        out = []
        # 1) urls[]
        for u in row_.get("urls") or []:
            if isinstance(u, dict) and u.get("text"):
                out.append(u["text"])
            elif isinstance(u, str):
                out.append(u)
        # 2) stickers[].text
        for s in row_.get("stickers") or []:
            t = s.get("text") if isinstance(s, dict) else None
            if t:
                out += URL_RE.findall(t)
        # 3) caption/ocr/raw_text_candidates
        for fld in ("caption_text", "ocr_text"):
            v = row_.get(fld)
            if v:
                out += URL_RE.findall(v)
        for t in row_.get("raw_text_candidates") or []:
            out += URL_RE.findall(str(t))
        # unique & trim
        seen, clean = set(), []
        for u in out:
            u2 = u.strip().strip(".,);]")
            if u2 and u2 not in seen:
                seen.add(u2)
                clean.append(u2)
        return clean

    def fetch_image_as_data_uri(url: str, max_bytes: int = 4_000_000) -> Optional[str]:
        try:
            r = requests.get(
                url,
                timeout=25,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": "https://www.instagram.com/",
                },
            )
            if r.status_code != 200 or not r.content:
                return None
            content = r.content
            ctype = (
                r.headers.get("Content-Type")
                or mimetypes.guess_type(url)[0]
                or "image/jpeg"
            )

            # compress if too large and PIL available
            if len(content) > max_bytes:
                try:
                    if (
                        "PIL_OK" in globals()
                        and globals().get("PIL_OK")
                        and "Image" in globals()
                    ):
                        im = globals()["Image"].open(BytesIO(content)).convert("RGB")
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
            _dlog(
                "fetch_image_as_data_uri failed",
                level="WARN",
                obj={"err": str(e), "url": url},
            )
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
        "model": MODEL,  # must be a Vision model (e.g., "gpt-4.1-mini")
        "temperature": 0,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content_parts},
        ],
        "response_format": {"type": "json_object"},
    }
    _dlog("OpenAI payload (short): model=", MODEL)

    # ---------- send with retries ----------
    for attempt in range(1, 6):
        try:
            if "rate_limit_sleep" in globals() and callable(
                globals()["rate_limit_sleep"]
            ):
                globals()["rate_limit_sleep"]()
            print("=" * 50)
            print(f"▶️ Attempt {attempt}")
            resp = requests.post(
                OPENAI_URL, headers=OA_HEADERS, json=payload, timeout=90
            )
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
                    start = body_text.index("{")
                    end = body_text.rindex("}") + 1
                    return json.loads(body_text[start:end])
                except Exception as e:
                    _dlog(
                        "JSON parse failed (even fallback).", level="ERROR", obj=str(e)
                    )
                    return None

        if resp.status_code == 429:
            _dlog("⚠️ 429 - Sleeping 5s", level="WARN")
            time.sleep(5)
            continue
        if resp.status_code in (500, 502, 503, 504):
            _dlog("⚠️ Server error - Sleeping 3s", level="WARN")
            time.sleep(3)
            continue

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
                continue

        _dlog(
            "❌ OpenAI error:",
            level="ERROR",
            obj={"status": resp.status_code, "body": _short(body_text, 800)},
        )
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


def main():
    try:
        rows = get_raw_rows(MAX_ROWS)
    except Exception as e:
        print("❌ Failed to fetch rows:", e)
        return
    dlog(
        "SB auth header startswith 'eyJ'? (anon-ish):",
        obj=str(SB_HEADERS.get("Authorization", ""))[:10],
    )

    print(f"Fetched {len(rows)} rows from {RAW_TABLE}")

    for idx, row in enumerate(rows, 1):
        media_id = row.get("media_id") or f"row_{idx}"
        print(f"\n[{idx}/{len(rows)}] Processing: {media_id}")
        # פריסת payload -> תמלא image_url/urls/stickers וכו' אם חסר
        row = normalize_row(row)
        dlog("After normalize_row keys:", obj=list(row.keys()))
        dlog(
            "Quick check fields:",
            obj={
                "image_url": row.get("image_url"),
                "urls": row.get("urls"),
                "stickers_len": len(row.get("stickers") or []),
                "caption_text": _short(row.get("caption_text")),
            },
        )

        try:
            if already_in_relevant(media_id):
                print("↩️  Skipped (already in relevant_story)")
                set_processing_status(
                    media_id, "skipped", None, {"reason": "already_in_relevant"}
                )
                continue
        except Exception as e:
            print("⚠️ relevant existence check failed:", e)

        try:
            result = call_openai_filter(row)
        except Exception as e:
            print("❌ OpenAI call failed:", e)
            set_processing_status(media_id, "error", str(e))
            continue

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
                if not result.get("coupon"):
                    result["coupon"] = result["coupons"][0]

        # כלל ביטחון: אם יש קופונים כלשהם -> רלוונטי
        if not result.get("is_relevant") and (
            result.get("coupons") or result.get("coupon_items")
        ):
            result["is_relevant"] = True
            result["content_type"] = "coupon"

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
