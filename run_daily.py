# run_daily.py — table-matched version (no processed column)

import os
import time
import random
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import requests

# ============== CONFIG (edit here) ==============
SUPABASE_URL = "https://dgxkdenkbaphzabkcybq.supabase.co".rstrip("/")
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRneGtkZW5rYmFwaHphYmtjeWJxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkwMTEwNjgsImV4cCI6MjA3NDU4NzA2OH0.5nU857rLJCM82icLoYuNAvFhbJOhy9ZVUn12JQ61uy4"

RAW_TABLE = "raw_story"
REL_TABLE = "relevant_story"

MODEL = "gpt-4o-mini"
MAX_ROWS = 50
# =================================================

SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
SB_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OA_HEADERS = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
}

# ----- Rate limiting -----
LAST_CALL_TS = 0.0
MIN_INTERVAL_S = 0.5  # עד 2 קריאות לשנייה

def rate_limit_sleep():
    global LAST_CALL_TS
    now = time.time()
    delta = now - LAST_CALL_TS
    if delta < MIN_INTERVAL_S:
        time.sleep(MIN_INTERVAL_S - delta)
    LAST_CALL_TS = time.time()

# ----- Utilities -----
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sb_get(path: str, params: Dict[str, str]) -> requests.Response:
    return requests.get(path, headers=SB_HEADERS, params=params, timeout=30)

def sb_patch(path: str, params: Dict[str, str], body: Dict[str, Any]) -> requests.Response:
    return requests.patch(path, headers=SB_HEADERS, params=params, data=json.dumps(body), timeout=30)

def sb_post(path: str, body: Dict[str, Any], prefer: Optional[str] = None, params: Optional[Dict[str, str]] = None) -> requests.Response:
    headers = SB_HEADERS.copy()
    if prefer:
        # מאפשר upsert
        headers["Prefer"] = prefer
    return requests.post(path, headers=headers, params=params, data=json.dumps(body), timeout=30)

# ----- Supabase I/O -----
def get_raw_rows(limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    """
    מושך את השורות האחרונות מהטבלה (בלי סינון לפי processed).
    נדלג אח"כ בקוד על מי שכבר טופל (קיים ב-relevant_story).
    """
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {
        "select": "*",
        "order": "taken_at_iso.desc",  # קיים אצלך כ-timestamptz
        "limit": str(limit),
    }
    r = sb_get(url, params)
    if r.status_code >= 400:
        print("❌ Supabase error (raw fetch):", r.status_code, r.text)
    r.raise_for_status()
    return r.json()

def set_processing_status(media_id: str, status: str, last_error: Optional[str] = None, extra: Optional[Dict[str, Any]] = None):
    """
    כותב לתוך השדה JSONB בשם 'processing' מעקב סטטוס:
    {
      "status": "ok|skipped|non_relevant|error",
      "last_error": "...",
      "ts": "...",
      ...extra
    }
    """
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
    """
    בדיקה האם ה-media_id כבר קיים בטבלת היעד כדי למנוע כפילות.
    """
    url = f"{SUPABASE_REST}/{REL_TABLE}"
    params = {
        "select": "media_id",
        "media_id": f"eq.{media_id}",
        "limit": "1",
    }
    r = sb_get(url, params)
    if r.status_code >= 400:
        print("⚠️ relevant lookup error:", r.status_code, r.text)
        # במקרה של שגיאה לא נבלום עיבוד
        return False
    arr = r.json()
    return bool(arr)

def upsert_relevant(row: Dict[str, Any], result: Dict[str, Any]):
    """
    upsert ל-relevant_story לפי media_id (דורש ייחודיות/אינדקס יוניק על media_id).
    """
    payload = {
        "media_id": row.get("media_id"),
        "user_id": row.get("user_id"),
        "username": row.get("username"),
        "taken_at_iso": row.get("taken_at_iso"),
        "type": row.get("type"),
        # מהמודל:
        "brand": result.get("brand"),
        "coupon_code": result.get("coupon_code"),
        "url": result.get("url"),
        "evidence": result.get("evidence"),
        "source": "openai_filter_json_bot",
        "model": MODEL,
    }
    url = f"{SUPABASE_REST}/{REL_TABLE}"
    # upsert: צריך on_conflict=media_id + Prefer: resolution=merge-duplicates
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

# ----- OpenAI -----
SYSTEM_PROMPT = """You are filter_json_bot.
Goal: Decide if a single Instagram media row is relevant.

Decision rule:
is_relevant = has_collab AND (has_coupon_code OR has_url).

Definitions (case-insensitive):
- has_collab if ANY signals appear:
  English: "paid partnership", "sponsored", "ad", "#ad", "#sponsored", "#paidpartnership", "partnered", "collab".
  Hebrew: "בשיתוף פעולה", "שת״פ", "שתפ", "תוכן ממומן", "פרסומת", "חסות", "בשיתוף עם", "פרסומי", hashtags like #שת״פ, #שתפ, #בשיתוף_פעולה.
- has_coupon_code if a concrete code pattern appears (e.g. SAVE20, FOX15). If only a coupon keyword appears but there's a URL, treat as coupon evidence.
- has_url if any valid URL is detected or a permalink exists.

Input policy:
You ONLY receive minimal fields: caption_text, ocr_text, stickers, hashtags, booleans has_image/has_video, and permalink_present.
Do NOT assume any other hidden fields.

Output JSON (strict):
{
  "is_relevant": boolean,
  "brand": string|null,
  "coupon_code": string|null,
  "url": string|null,
  "evidence": { "collab": string[], "coupon": string[], "url": string[] }
}
Return ONLY valid JSON with those keys, no extra commentary.
"""

def minimal_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    has_image = bool(row.get("image_url"))
    has_video = bool(row.get("video_url"))
    return {
        "caption_text": row.get("caption_text"),
        "ocr_text": row.get("ocr_text"),
        "stickers": row.get("stickers"),
        "hashtags": row.get("hashtags"),
        "has_image": has_image,
        "has_video": has_video,
        "permalink_present": bool(row.get("permalink")),
    }

def call_openai_filter(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user_content = {"row_minimal": minimal_payload(row)}
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ],
    }

    max_attempts = 6
    backoff = 1.5

    for attempt in range(1, max_attempts + 1):
        rate_limit_sleep()
        resp = requests.post(OPENAI_URL, headers=OA_HEADERS, json=payload, timeout=60)

        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                # נסיון לחלץ JSON נקי מתוך טקסט
                try:
                    start = content.index("{")
                    end = content.rindex("}") + 1
                    return json.loads(content[start:end])
                except Exception:
                    print("❌ JSON parse error from model:", content[:250])
                    return None

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                sleep_s = float(retry_after)
            else:
                jitter = random.uniform(0, backoff * 0.4)
                sleep_s = backoff + jitter
                backoff = min(backoff * 2, 30.0)
            print(f"⚠️ 429 rate-limited. Sleeping {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
            time.sleep(sleep_s)
            continue

        if resp.status_code in (500, 502, 503, 504):
            jitter = random.uniform(0, backoff * 0.4)
            sleep_s = backoff + jitter
            backoff = min(backoff * 2, 30.0)
            print(f"⚠️ {resp.status_code} server error. Sleeping {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
            time.sleep(sleep_s)
            continue

        print(f"❌ OpenAI error {resp.status_code}: {resp.text[:300]}")
        return None

    raise RuntimeError("Request failed after retries")

# ----- Filtering policy (pre-checks before model) -----
def has_any_textual_signal(row: Dict[str, Any]) -> bool:
    if row.get("caption_text"):
        return True
    if row.get("ocr_text"):
        return True
    stickers = row.get("stickers") or []
    if isinstance(stickers, list) and len(stickers) > 0:
        return True
    hashtags = row.get("hashtags") or []
    if isinstance(hashtags, list) and len(hashtags) > 0:
        return True
    return False

def should_process(row: Dict[str, Any]) -> bool:
    has_img = bool(row.get("image_url"))
    has_vid = bool(row.get("video_url"))
    if not (has_img or has_vid):
        return False
    return has_any_textual_signal(row)

# ----- MAIN -----
def main():
    try:
        rows = get_raw_rows(MAX_ROWS)
    except Exception as e:
        print("❌ Failed to fetch rows:", e)
        return

    print(f"Fetched {len(rows)} rows from {RAW_TABLE}")

    for idx, row in enumerate(rows, 1):
        media_id = row.get("media_id") or f"row_{idx}"
        print(f"\n[{idx}/{len(rows)}] Processing: {media_id}")

        # דלג אם כבר קיים בטבלת היעד
        try:
            if already_in_relevant(media_id):
                print("↩️  Skipped (already in relevant_story).")
                set_processing_status(media_id, "skipped", None, {"reason": "already_in_relevant"})
                continue
        except Exception as e:
            print("⚠️ relevant existence check failed:", e)

        # דילוג מוקדם אם לא עומד בתנאי העיבוד
        if not should_process(row):
            print("↩️  Skipped (no image/video or no textual signals).")
            set_processing_status(media_id, "skipped", None, {"reason": "no_media_or_text"})
            continue

        # קריאה למודל
        try:
            result = call_openai_filter(row)
        except Exception as e:
            print("❌ OpenAI call failed:", e)
            set_processing_status(media_id, "error", str(e))
            continue

        if not result:
            print("↩️  No result from model.")
            set_processing_status(media_id, "error", "no_result_from_model")
            continue

        # החלטה
        is_rel = bool(result.get("is_relevant"))
        if is_rel:
            try:
                upsert_relevant(row, result)
                set_processing_status(media_id, "ok", None, {"decision": "relevant"})
                print("✅ Upserted into relevant_story.")
            except Exception as e:
                print("❌ Insert/Upsert relevant failed:", e)
                set_processing_status(media_id, "error", f"insert_relevant_failed: {e}")
        else:
            print("ℹ️ Marked non-relevant.")
            set_processing_status(media_id, "non_relevant", None)

if __name__ == "__main__":
    main()
