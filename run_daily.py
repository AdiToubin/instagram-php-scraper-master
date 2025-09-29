import os
import time
import json
import re
import requests
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from requests.exceptions import RequestException, Timeout

# ============== CONFIG ==============
SUPABASE_URL = "https://dgxkdenkbaphzabkcybq.supabase.co".rstrip("/")
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRneGtkZW5rYmFwaHphYmtjeWJxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkwMTEwNjgsImV4cCI6MjA3NDU4NzA2OH0.5nU857rLJCM82icLoYuNAvFhbJOhy9ZVUn12JQ61uy4"  # 🔒 החלף ב-SERVICE_ROLE שלך

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "YOUR_OPENAI_API_KEY"
if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY")

RAW_TABLE = "stories_raw"
REL_TABLE = "relevant_story"
MODEL = "gpt-4o-mini"
MAX_ROWS = 50

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
# ====================================

LAST_CALL_TS = 0.0
MIN_INTERVAL_S = 0.5

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
        "name": result.get("name") or row.get("username"),
        "brand": result.get("brand"),
        "coupon": result.get("coupon"),
        "url": result.get("url"),
        "date": row.get("taken_at_iso"),
        "Description": result.get("Description") or row.get("caption_text") or row.get("ocr_text"),
    }
    url = f"{SUPABASE_REST}/{REL_TABLE}"
    r = sb_post(url, payload, prefer="return=representation,resolution=merge-duplicates", params={"on_conflict": "media_id"})
    if r.status_code >= 400:
        print("❌ upsert relevant failed:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()
#############################################################
SYSTEM_PROMPT = """You are filter_json_bot.
Goal: Decide if a single Instagram media row is relevant and extract structured fields for database insertion.

Relevance rule:
is_relevant = has_collab AND (has_coupon_code OR has_url)

Definitions (case-insensitive):
- has_collab: if ANY of these signals appear:
  Hebrew: "בשיתוף פעולה", "שת״פ", "שתפ", "תוכן ממומן", "פרסומת", "חסות", "בשיתוף עם", "פרסומי"
  English: "paid partnership", "sponsored", "ad", "#ad", "#sponsored", "#paidpartnership", "partnered", "collab"
  Hashtags: #ad, #ads, #sponsored, #partner, #paidpartnership, #שת״פ, #שתפ, #בשיתוף_פעולה

- has_coupon_code: if a concrete code (e.g. SAVE20, FOX15) appears.
- has_url: if there's any valid URL or permalink.

Input:
You get: caption_text, ocr_text, stickers, hashtags, has_image, has_video, permalink_present

Output (strict JSON):
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
        "has_image": bool(row.get("image_url")),
        "has_video": bool(row.get("video_url")),
        "permalink_present": bool(row.get("permalink")),
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

def call_openai_filter(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user_content = {"row_minimal": minimal_payload(row)}
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)},
        ],
    }

    for attempt in range(1, 7):
        try:
            rate_limit_sleep()
            print("=" * 50)
            print(f"▶️ Attempt {attempt}")
            resp = requests.post(OPENAI_URL, headers=OA_HEADERS, json=payload, timeout=90)
            print("⬅️ Status:", resp.status_code)
        except Timeout:
            print("⏱️ Timeout. Retrying...")
            time.sleep(2)
            continue
        except RequestException as e:
            print("❌ Network error:", e)
            return None

        if resp.status_code == 200:
            try:
                content = resp.json()["choices"][0]["message"]["content"]
                return json.loads(content)
            except Exception:
                return _extract_json(resp.text)
        elif resp.status_code == 429:
            print("⚠️ 429 - Sleeping 5s")
            time.sleep(5)
        elif resp.status_code in (500, 502, 503, 504):
            print("⚠️ Server error - Sleeping")
            time.sleep(3)
        else:
            print("❌ OpenAI error:", resp.status_code, resp.text)
            return None

    raise RuntimeError("Request failed after retries")

def should_process(row: Dict[str, Any]) -> bool:
    return bool(row.get("image_url") or row.get("video_url"))

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
