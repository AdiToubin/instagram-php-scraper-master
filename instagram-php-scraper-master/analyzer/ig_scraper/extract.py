"""
Story extraction - Python port of the per-item transform loop in
stories_with_stickers.php (everything between the API call and the final
json_encode). Faithful translation, not a rewrite: same field names, same
regexes, same source ordering, so the output row shape matches what
story_raw already expects and run_daily.py doesn't need to change.

Not ported: the IG_DEBUG/IG_SAVE_RAW debug-dump side effects (they write
diagnostic files to disk and don't affect the returned story objects).
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import ocr
from .brands import BrandDetector

EXTRACTION_VERSION = "1.1.0-py"

_HEBREW_RE = re.compile(r"[֐-׿]")
_HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)
_MENTION_RE = re.compile(r"@([\w.]+)", re.UNICODE)

_PRICE_RE_1 = re.compile(r"(?:^|[\s])(?:₪|\$|€)\s*\d+(?:[.,]\d+)?", re.UNICODE)
_PRICE_RE_2 = re.compile(r"\d+(?:[.,]\d+)?\s*(?:₪|ש\"ח|\$|€)", re.UNICODE)
_PERCENT_RE = re.compile(r"\b\d{1,3}\s?%\b")
_COUPON_WORD_RE = re.compile(r"\b(coupon|קופון|promo|voucher)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(20\d{2}|19\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b")

_URL_START_RE = re.compile(r"^https?://", re.IGNORECASE)
_CDN_HOST_RE = re.compile(r"(^|\.)(cdninstagram\.com|fbcdn\.net|fna\.fbcdn\.net)$", re.IGNORECASE)
_MEDIA_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp|mp4|mov)(\?|$)", re.IGNORECASE)

_COUPON_CODE_FROM_TEXT_RE = re.compile(
    r"(?:קופון|ה?קוד(?:\s+(?:הנחה|קופון|שלי|שלנו))?|coupon|promo(?:code)?|voucher)"
    r"\s*[:：]?\s*([A-Za-z0-9_-]{3,20})",
    re.IGNORECASE | re.UNICODE,
)

_TEXT_EMAIL_NORM_RE = re.compile(r"\b([A-Za-z0-9._]{2,30})@")
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_MENTION_IN_TEXT_RE = re.compile(r"@([A-Za-z0-9._]{2,30})")

_ACCESSIBILITY_PATTERNS = [
    re.compile(r"טקסט שאומר\s+['\"“”]([^'\"]+)['\"“”]", re.UNICODE),
    re.compile(r"text that says\s+['\"“”]([^'\"]+)['\"“”]", re.IGNORECASE),
    re.compile(r"text that reads\s+['\"“”]([^'\"]+)['\"“”]", re.IGNORECASE),
    re.compile(r"reads\s+['\"“”]([^'\"]+)['\"“”]", re.IGNORECASE),
]

BAD_IMAGE_HOSTS = {
    "instagram.com", "www.instagram.com", "cdninstagram.com",
    "fbcdn.net", "fna.fbcdn.net", "scontent.cdninstagram.com",
}


# ============== SMALL HELPERS (mirror the top of stories_with_stickers.php) ==============

def _dig(obj: Any, *keys: Any, default: Any = None) -> Any:
    cur = obj
    for k in keys:
        if isinstance(k, int):
            if isinstance(cur, list) and -len(cur) <= k < len(cur):
                cur = cur[k]
            else:
                return default
        else:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
    return cur


def first_non_empty(*vals: Any) -> Any:
    for v in vals:
        if v is not None and v != "":
            return v
    return None


def is_hebrew(s: str) -> bool:
    return bool(_HEBREW_RE.search(s))


def lang_guess(t: Optional[str]) -> Optional[str]:
    if t is None or t.strip() == "":
        return None
    if is_hebrew(t):
        return "he"
    letters = re.sub(r"[^A-Za-z]", "", t)
    if letters and len(letters) >= max(3, int(len(t) * 0.2)):
        return "en"
    return None


def uniq_strings(items: List[Any]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in items:
        v = str(v)
        k = v.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(v)
    return out


def collect_hashtags_from_caption(c: Optional[str]) -> List[str]:
    if not c:
        return []
    return uniq_strings(_HASHTAG_RE.findall(c))


def collect_mentions_from_caption(c: Optional[str]) -> List[str]:
    if not c:
        return []
    return uniq_strings(_MENTION_RE.findall(c))


def resolve_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        h = urlparse(url).hostname
    except ValueError:
        return None
    return h or None


def to_iso(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def bbox_or_default(src: Optional[Dict[str, Any]]) -> List[float]:
    if not src:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        float(src.get("x") or 0),
        float(src.get("y") or 0),
        float(src.get("width") or 0),
        float(src.get("height") or 0),
    ]


def sticker_text_of(src: Optional[Dict[str, Any]]) -> str:
    if not src:
        return ""
    t = first_non_empty(src.get("title"), src.get("text"), src.get("name"), src.get("question"))
    return str(t) if t is not None else ""


def classify_sticker(text: Optional[str], url: Optional[str] = None) -> str:
    t = (text or "").lower()
    if url:
        return "url"
    if t == "":
        return "generic"
    if _PRICE_RE_1.search(t) or _PRICE_RE_2.search(t):
        return "price"
    if _PERCENT_RE.search(t):
        return "percent"
    if _COUPON_WORD_RE.search(t):
        return "coupon"
    if _DATE_RE.search(t):
        return "date"
    return "generic"


def harvest_urls_deep(data: Any, out: Dict[str, Dict[str, Any]]) -> None:
    if isinstance(data, str):
        if _URL_START_RE.match(data):
            u = data.strip()
            host = (resolve_domain(u) or "").lower()
            is_cdn = bool(_CDN_HOST_RE.search(host))
            is_media = bool(_MEDIA_EXT_RE.search(u))
            if not (is_cdn and is_media):
                out[u.lower()] = {"text": u, "resolved_domain": resolve_domain(u)}
        return
    if isinstance(data, dict):
        for v in data.values():
            harvest_urls_deep(v, out)
    elif isinstance(data, list):
        for v in data:
            harvest_urls_deep(v, out)


def unwrap_instagram_shim(url: str) -> str:
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    if (parts.hostname or "").lower() == "l.instagram.com":
        q = parse_qs(parts.query)
        if q.get("u"):
            return unquote(q["u"][0])
    return url


def _coupon_charset_ok(t: str) -> Optional[str]:
    """Shared trim/length/charset checks - just the shape, not the
    letter/digit mix. Returns the trimmed, uppercased candidate or None."""
    t = t.strip(" \t\n\r\x00\x0b-_")
    if len(t) < 4 or len(t) > 20:
        return None
    if not re.match(r"^[A-Za-z0-9_-]+$", t):
        return None
    return t.upper()


def clean_coupon_code(t: str) -> Optional[str]:
    """Strict, faithful port of cleanCouponCode() in
    stories_with_stickers.php: requires both a letter and a digit. Used for
    context-free checks - a whole sticker's text, or a single word scanned
    with no nearby coupon keyword - where that strictness matters: without
    it, a live test showed ordinary words (names, "STYLE", "PHOTO"...)
    getting flagged as coupon codes."""
    code = _coupon_charset_ok(t)
    if code is None:
        return None
    if not re.search(r"[A-Za-z]", code):
        return None
    if not re.search(r"[0-9]", code):
        return None
    return code


def clean_coupon_code_near_keyword(t: str) -> Optional[str]:
    """Looser variant used only by coupon_codes_from_text(), where the
    candidate already sits right after a "קוד"/"קופון"/"coupon" keyword -
    that context justifies accepting letter-only or digit-only codes like
    "RESIVAN" and "6868", real currently-active codes the strict letter+digit
    rule missed (confirmed in a live shadow test). Mirrors the letter-or-digit
    heuristic from run_daily.py's is_valid_coupon_code(), so both validators
    agree on what counts as a code."""
    code = _coupon_charset_ok(t)
    if code is None:
        return None
    if re.match(r"^\d{2}-\d{2}$", code):
        return None
    if re.match(r"^[0-9]+[A-Z]+[0-9]+[A-Z]+", code):
        return None
    digits_only = code.replace("-", "")
    if digits_only.isdigit():
        return code if len(digits_only) <= 6 else None
    if sum(1 for c in code if c.isalpha()) < 2:
        return None
    return code


def coupon_codes_from_text(t: str) -> List[str]:
    out = []
    seen = set()
    for m in _COUPON_CODE_FROM_TEXT_RE.finditer(t):
        clean = clean_coupon_code_near_keyword(m.group(1))
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def urls_from_text(text: str) -> List[Dict[str, Any]]:
    urls: Dict[str, Dict[str, Any]] = {}
    text_norm = _TEXT_EMAIL_NORM_RE.sub(r"@\1", text)
    for m in _URL_IN_TEXT_RE.finditer(text_norm):
        u = m.group(0).strip()
        urls[u.lower()] = {"text": unwrap_instagram_shim(u), "resolved_domain": resolve_domain(u)}
    for m in _EMAIL_RE.finditer(text_norm):
        e = m.group(0).strip()
        mailto = "mailto:" + e
        domain_part = e.split("@", 1)[1] if "@" in e else ""
        urls[mailto.lower()] = {"text": mailto, "resolved_domain": resolve_domain("http://" + domain_part)}
    for m in _MENTION_IN_TEXT_RE.finditer(text_norm):
        h = m.group(1)
        u = "https://www.instagram.com/" + h
        urls[u.lower()] = {"text": u, "resolved_domain": "www.instagram.com"}
    return list(urls.values())


def is_bad_image_host(url: str) -> bool:
    try:
        h = (urlparse(url).netloc or "").lower()
    except ValueError:
        return True
    if h in BAD_IMAGE_HOSTS:
        return True
    return any(h.endswith("." + bh) for bh in BAD_IMAGE_HOSTS)


def _get_tray(raw: Dict[str, Any], user_id: str) -> List[Dict[str, Any]]:
    reels = raw.get("reels") or {}
    if isinstance(reels, dict) and str(user_id) in reels:
        items = _dig(reels, str(user_id), "items")
        if items is not None:
            return items
    reels_media = raw.get("reels_media")
    if isinstance(reels_media, list) and reels_media:
        items = _dig(reels_media, 0, "items")
        if items is not None:
            return items
    return raw.get("items") or []


def _extract_static_text_stickers(items: Any, stickers: List[Dict[str, Any]]) -> None:
    """Shared logic for story_static_models / story_overlay_stickers / story_text_stickers."""
    if not items:
        return
    for sm in items:
        text = str(first_non_empty(sm.get("text"), sm.get("display_text"), sm.get("sticker_text"), "") or "").strip()
        if text == "":
            continue
        bbox = bbox_or_default(sm)
        stickers.append({"type": "generic", "text": text, "bbox": bbox, "confidence": 1.0})
        for c in coupon_codes_from_text(text):
            stickers.append({"type": "coupon", "text": c, "bbox": bbox, "confidence": 1.0})
        cc = clean_coupon_code(text)
        if cc:
            stickers.append({"type": "coupon", "text": cc, "bbox": bbox, "confidence": 1.0})


# ============== MAIN EXTRACTION ==============

def extract_stories(raw: Dict[str, Any], user_id_arg: str, username_arg: Optional[str] = None) -> List[Dict[str, Any]]:
    """Python port of the foreach($tray as $it) loop. `raw` is the exact dict
    returned by ig_scraper.client.fetch_stories(user_id_arg)."""
    tray = _get_tray(raw, user_id_arg)
    brand_detector = BrandDetector()
    return [_extract_one(it, user_id_arg, username_arg, brand_detector) for it in tray]


def _extract_one(
    it: Dict[str, Any],
    user_id_arg: str,
    username_arg: Optional[str],
    brand_detector: BrandDetector,
) -> Dict[str, Any]:
    media_id = it.get("id")
    owner = it.get("user") or {}
    user_pk = first_non_empty(owner.get("pk"), owner.get("pk_id"), owner.get("id"))
    username = owner.get("username") or username_arg

    width = it.get("original_width") or 0
    height = it.get("original_height") or 0
    dur_ms = None
    if it.get("video_duration") is not None:
        dur_ms = int(round(float(it["video_duration"]) * 1000))

    image_url = first_non_empty(
        _dig(it, "image_versions2", "candidates", 0, "url"),
        it.get("display_url"),
        it.get("image_url"),
    )
    video_url = first_non_empty(_dig(it, "video_versions", 0, "url"), it.get("video_url"))

    caption_text = None
    if it.get("caption") is not None:
        cap = it["caption"]
        caption_text = cap.get("text") if isinstance(cap, dict) else str(cap)
        if caption_text is not None:
            caption_text = caption_text.strip()

    taken_at_iso = to_iso(it.get("taken_at"))
    expiring_at_iso = to_iso(it.get("expiring_at"))

    # ----- URLs: same source order as stories_with_stickers.php -----
    urls: Dict[str, Dict[str, Any]] = {}

    for cta in it.get("story_cta") or []:
        for lnk in cta.get("links") or []:
            u = first_non_empty(
                lnk.get("webUri"), lnk.get("url"), lnk.get("link_url"),
                _dig(lnk, "story_link", "link_context", "url"),
            )
            if u:
                un = unwrap_instagram_shim(u)
                urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    for ls in it.get("story_link_stickers") or []:
        u = first_non_empty(
            _dig(ls, "story_link", "url"), ls.get("url"), ls.get("link_url"),
            _dig(ls, "story_link", "link_context", "url"),
        )
        if u:
            un = unwrap_instagram_shim(u)
            urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    for to in it.get("tappable_objects") or []:
        obj_type = to.get("object_type") or ""
        if obj_type == "link":
            u = first_non_empty(_dig(to, "link", "url"), to.get("url"))
            if u:
                un = unwrap_instagram_shim(u)
                urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}
        elif obj_type in ("product", "shopping", "storefront", "external_link", "web_link"):
            u = first_non_empty(
                _dig(to, "product", "external_url"), _dig(to, "shopping", "url"),
                _dig(to, "storefront", "url"), _dig(to, "external_link", "url"),
                _dig(to, "web_link", "url"), to.get("url"),
            )
            if u:
                un = unwrap_instagram_shim(u)
                urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    for bl in it.get("story_bloks_stickers") or []:
        data = _dig(bl, "bloks_sticker", "bloks_data", default=bl.get("bloks_data"))
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                data = None
        deep_map: Dict[str, Dict[str, Any]] = {}
        if data:
            harvest_urls_deep(data, deep_map)
        u = first_non_empty(bl.get("url"), bl.get("link_url"))
        if u:
            un = unwrap_instagram_shim(u)
            deep_map[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}
        urls.update(deep_map)

    for app in it.get("story_app_attribution") or []:
        u = first_non_empty(app.get("url"), app.get("link"), app.get("app_action_url"))
        if u:
            un = unwrap_instagram_shim(u)
            urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    for shop in it.get("story_shopping_stickers") or []:
        u = first_non_empty(_dig(shop, "shopping_sticker", "url"), shop.get("url"), shop.get("external_url"))
        if u:
            un = unwrap_instagram_shim(u)
            urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    for cta in it.get("story_cta_stickers") or []:
        u = first_non_empty(_dig(cta, "cta_sticker", "url"), cta.get("url"), cta.get("action_url"))
        if u:
            un = unwrap_instagram_shim(u)
            urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    swipe = it.get("swipe_up_link")
    if swipe:
        u = (swipe.get("url") or swipe.get("link_url")) if isinstance(swipe, dict) else swipe
        if u:
            un = unwrap_instagram_shim(u)
            urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    action = it.get("action_link")
    if action:
        u = (action.get("url") or action.get("link_url")) if isinstance(action, dict) else action
        if u:
            un = unwrap_instagram_shim(u)
            urls[un.lower()] = {"text": un.strip(), "resolved_domain": resolve_domain(un)}

    for k in ("story_product_items", "story_feed_media", "story_music_stickers",
              "story_shopping_stickers", "story_cta_stickers"):
        if it.get(k):
            deep_map = {}
            harvest_urls_deep(it[k], deep_map)
            urls.update(deep_map)

    if caption_text:
        for u in urls_from_text(caption_text):
            urls[u["text"].lower()] = u

    # ----- hashtags & mentions -----
    hashtags = []
    for h in it.get("story_hashtags") or []:
        n = _dig(h, "hashtag", "name")
        if n:
            hashtags.append(n)
    hashtags = uniq_strings(hashtags + collect_hashtags_from_caption(caption_text))

    mentions = []
    for m in it.get("reel_mentions") or []:
        u = _dig(m, "user", "username")
        if u:
            mentions.append(u)
    for to in it.get("tappable_objects") or []:
        if (to.get("object_type") or "") == "mention":
            u = first_non_empty(_dig(to, "user", "username"), to.get("username"))
            if u:
                mentions.append(u)
    mentions = uniq_strings(mentions + collect_mentions_from_caption(caption_text))

    # ----- stickers (structured) -----
    stickers: List[Dict[str, Any]] = []

    for cta in it.get("story_cta") or []:
        for lnk in cta.get("links") or []:
            u = first_non_empty(
                lnk.get("webUri"), lnk.get("url"), lnk.get("link_url"),
                _dig(lnk, "story_link", "link_context", "url"),
            )
            un = unwrap_instagram_shim(u) if u else None
            text = sticker_text_of(lnk)
            if un or text:
                stickers.append({
                    "type": classify_sticker(text, un), "text": text or (un or ""),
                    "bbox": [0, 0, 0, 0], "confidence": 0.0,
                })

    for ls in it.get("story_link_stickers") or []:
        u = first_non_empty(
            _dig(ls, "story_link", "url"), ls.get("url"), ls.get("link_url"),
            _dig(ls, "story_link", "link_context", "url"),
        )
        un = unwrap_instagram_shim(u) if u else None
        text = sticker_text_of(ls)
        stickers.append({
            "type": classify_sticker(text, un), "text": text or (un or ""),
            "bbox": bbox_or_default(ls), "confidence": 0.0,
        })

    for to in it.get("tappable_objects") or []:
        type_obj = to.get("object_type") or ""
        text = sticker_text_of(to)
        u = first_non_empty(_dig(to, "link", "url"), to.get("url"))
        un = unwrap_instagram_shim(u) if u else None
        if type_obj == "link" or un:
            stickers.append({"type": "url", "text": un or text, "bbox": bbox_or_default(to), "confidence": 0.0})
        elif text != "":
            stickers.append({"type": classify_sticker(text), "text": text, "bbox": bbox_or_default(to), "confidence": 0.0})

    for p in it.get("story_polls") or []:
        s = p.get("poll_sticker") or {}
        text = ((s.get("question") or "") + " " + " ".join(t.get("text", "") for t in s.get("tallies") or [])).strip()
        if text != "":
            stickers.append({"type": "generic", "text": text, "bbox": bbox_or_default(s), "confidence": 0.0})

    for s_ in it.get("story_sliders") or []:
        st = s_.get("slider_sticker") or {}
        text = ((st.get("question") or "") + " " + (st.get("emoji") or "")).strip()
        if text != "":
            stickers.append({"type": "generic", "text": text, "bbox": bbox_or_default(st), "confidence": 0.0})

    quiz_arr = it.get("story_quizs") or it.get("story_quiz") or []
    for q in quiz_arr:
        st = q.get("quiz_sticker") or {}
        choices = [t.get("text", "") for t in st.get("tallies") or []]
        text = ((st.get("question") or "") + " " + " ".join(choices)).strip()
        if text != "":
            stickers.append({"type": "generic", "text": text, "bbox": bbox_or_default(st), "confidence": 0.0})

    for q in it.get("story_questions") or []:
        st = q.get("question_sticker") or {}
        text = st.get("question") or st.get("question_text") or ""
        if text != "":
            stickers.append({"type": "generic", "text": text, "bbox": bbox_or_default(st), "confidence": 0.0})

    _extract_static_text_stickers(it.get("story_static_models"), stickers)
    _extract_static_text_stickers(it.get("story_overlay_stickers"), stickers)
    _extract_static_text_stickers(it.get("story_text_stickers"), stickers)

    # ----- OCR (image/video) -----
    raw_text_candidates: List[str] = [caption_text] if caption_text else []
    has_text = False
    ocr_parts: List[str] = []
    proc_errors: List[str] = []

    if it.get("accessibility_caption") is not None:
        accessibility_text = str(it["accessibility_caption"]).strip()
        extracted_text = None
        for pat in _ACCESSIBILITY_PATTERNS:
            m = pat.search(accessibility_text)
            if m:
                extracted_text = m.group(1).strip()
                break
        if not extracted_text and len(accessibility_text) < 300 and not re.search(r"https?://", accessibility_text):
            extracted_text = accessibility_text
        if extracted_text:
            raw_text_candidates.append(extracted_text)
            has_text = True
            ocr_parts.append(extracted_text)
            for u in urls_from_text(extracted_text):
                urls[u["text"].lower()] = u
            for c in coupon_codes_from_text(extracted_text):
                stickers.append({"type": "coupon", "text": c, "bbox": [0, 0, 0, 0], "confidence": 0.9})
            for word in re.split(r"\s+", extracted_text):
                cc = clean_coupon_code(word)
                if cc:
                    stickers.append({"type": "coupon", "text": cc, "bbox": [0, 0, 0, 0], "confidence": 0.7})
            stickers.append({"type": "generic", "text": extracted_text, "bbox": [0, 0, 0, 0], "confidence": 0.9})

    if ocr.tesseract_available():
        if image_url and (not video_url or not dur_ms):
            txt = ocr.ocr_image_url(image_url)
            if txt:
                raw_text_candidates.append(txt)
                has_text = True
                ocr_parts.append(txt)
                for u in urls_from_text(txt):
                    urls[u["text"].lower()] = u
                for c in coupon_codes_from_text(txt):
                    stickers.append({"type": "coupon", "text": c, "bbox": [0, 0, 0, 0], "confidence": 0.0})
        elif video_url:
            frame_txt = ocr.extract_video_frame_text(video_url, dur_ms or 60000)
            if not frame_txt:
                proc_errors.append("ffmpeg_extract_failed")
            if frame_txt:
                raw_text_candidates.append(frame_txt)
                has_text = True
                ocr_parts.append(frame_txt)
                for u in urls_from_text(frame_txt):
                    urls[u["text"].lower()] = u
                for c in coupon_codes_from_text(frame_txt):
                    stickers.append({"type": "coupon", "text": c, "bbox": [0, 0, 0, 0], "confidence": 0.0})
            elif image_url:
                txt = ocr.ocr_image_url(image_url)
                if txt:
                    raw_text_candidates.append(txt)
                    has_text = True
                    ocr_parts.append(txt)
                    for u in urls_from_text(txt):
                        urls[u["text"].lower()] = u
                    for c in coupon_codes_from_text(txt):
                        stickers.append({"type": "coupon", "text": c, "bbox": [0, 0, 0, 0], "confidence": 0.0})

    raw_text_candidates = uniq_strings(raw_text_candidates + [s.get("text", "") for s in stickers if s.get("text")])

    for candidate_text in raw_text_candidates:
        hashtags = uniq_strings(hashtags + collect_hashtags_from_caption(candidate_text))
        mentions = uniq_strings(mentions + collect_mentions_from_caption(candidate_text))

    have_url_sticker = {
        s["text"].lower() for s in stickers if s.get("type") == "url" and s.get("text")
    }
    for u in urls.values():
        k = u["text"].lower()
        if k not in have_url_sticker:
            stickers.append({"type": "url", "text": u["text"], "bbox": [0, 0, 0, 0], "confidence": 0.0})

    frames_used: List[int] = []
    if dur_ms and dur_ms > 0:
        seen_frames = set()
        for c in (0, min(45000, max(0, dur_ms - 1)), min(90000, max(0, dur_ms - 1))):
            if c not in seen_frames:
                seen_frames.add(c)
                frames_used.append(c)

    ocr_text = "\n".join(ocr_parts) if ocr_parts else None
    has_text = has_text or bool(raw_text_candidates)
    lang = lang_guess(raw_text_candidates[0] if raw_text_candidates else None)

    hash_base = json.dumps(
        {
            "media_id": media_id,
            "urls": [u["text"] for u in urls.values()],
            "hashtags": hashtags,
            "mentions": mentions,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha1(hash_base.encode("utf-8")).hexdigest()

    proc_err: List[str] = [] if ocr.tesseract_available() else ["ocr_not_enabled"]
    seen_err = set(proc_err)
    for e in proc_errors:
        if e not in seen_err:
            seen_err.add(e)
            proc_err.append(e)

    return {
        "media_id": str(media_id or ""),
        "user_id": str(user_pk) if user_pk else str(user_id_arg),
        "username": username,
        "type": "story",
        "taken_at_iso": taken_at_iso,
        "expiring_at_iso": expiring_at_iso,
        "image_url": None if video_url is not None else (image_url or None),
        "video_url": video_url or None,
        "ocr_text": ocr_text,
        "stickers": stickers,
        "urls": list(urls.values()),
        "raw_text_candidates": raw_text_candidates,
        "hashtags": hashtags,
        "mentions": mentions,
        "frames_used": frames_used,
        "media_meta": {"width": int(width or 0), "height": int(height or 0), "duration_ms": int(dur_ms or 0)},
        "language_guess": lang,
        "brand_candidates": brand_detector.detect_brands(image_url or "", " ".join(raw_text_candidates)),
        "source_flags": {"has_text": bool(has_text), "has_stickers": bool(stickers), "has_logo_hint": False},
        "content_hash": content_hash,
        "processing": {"extraction_version": EXTRACTION_VERSION, "errors": proc_err},
    }
