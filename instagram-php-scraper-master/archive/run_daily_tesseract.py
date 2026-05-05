#!/usr/bin/env python3
"""
run_daily_tesseract.py - Experimental Tesseract OCR + GPT-4o-mini approach
Cost-effective alternative to GPT-4o vision API

Architecture:
1. Tesseract OCR extracts text from images locally (free)
2. GPT-4o-mini analyzes text-only (much cheaper than vision)
3. Same business logic as run_daily.py

Dependencies:
    pip install pytesseract pillow
    Install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki
"""

import os
import re
import json
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv

# Tesseract OCR
try:
    import pytesseract
    from PIL import Image
    import io
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    print("⚠️ Tesseract not installed. Run: pip install pytesseract pillow")

load_dotenv()

# ============== CONFIG ==============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
IG_SESSIONID = os.getenv("IG_SESSIONID")
TESSERACT_PATH = os.getenv("TESSERACT_PATH")

# Set Tesseract path if provided
if TESSERACT_PATH and TESSERACT_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

RAW_TABLE = "raw_story"
REL_TABLE = "relevant_story"
REC_TABLE = "story_recommendations"

# ============== SYSTEM PROMPT (Text-Only) ==============
SYSTEM_PROMPT = """You are an Instagram story analyzer.
Your output MUST be valid JSON only.

You will receive:
- OCR text extracted from the story image
- Caption text
- Sticker texts
- URLs
- Context from previous stories

Your task has TWO SEPARATE GOALS:

================================================
1) COUPON DETECTION — HIGH SENSITIVITY (AGGRESSIVE)
================================================
You MUST mark the story as `is_relevant = true` if ANY of the following appear:

- A clear coupon code (any word that looks like a code)
- Partial code (e.g., "הקוד מתחיל ב…", "code in comments")
- Text that resembles a coupon structure (SAVE10, SIVAN22, ABCD10, etc.)
- Mentions of "קוד", "קופון", "coupon", even without full code

RULE:
False positives are acceptable. Missing a real coupon is NOT acceptable.
BUT:
- **PERCENTAGES ARE NOT CODES:** "20%", "30% OFF", "15%". NEVER put "20%" in the `coupon` field.
- **PHONE NUMBERS ARE NOT CODES:** Strings of digits like "0541234567" are NOT coupons.
- **HEBREW/SENTENCES ARE NOT CODES:** "קוד קופון", "שולחן עשר עיקרים" are NOT codes. 
  - A code must be **SHORT** (under 20 chars) and **ALPHANUMERIC** (e.g., "SAVE20", "DANIT10").

================================================
2) DESCRIPTION — NATURAL & MARKETING STYLE
================================================
You must write a Hebrew description that sounds like a HUMAN social media manager.
**CONSISTENCY RULE:** Do NOT write "קוד קופון מיוחד!" in the description if `coupon` is NULL.

✅ GOOD Examples (Natural):
- "המלצה על קרם לחות של קליניק - מעניק זוהר"
- "קוד קופון לנעלי אדידס - 30% הנחה על כל האתר"
- "אווירת בוקר עם קפה ומאפה"

❌ BAD Examples:
- "תמונה המציגה אישה מחזיקה קרם" (Too robotic)
- "" (Empty)

================================================
3) BRAND HANDLING
================================================
- **NEVER use "General", "Unknown", "Brand".** Look harder!
- IGNORE platforms like Humanz, Linktree. Look for the REAL brand.

================================================
4) JSON OUTPUT FORMAT:
================================================
{
  "is_relevant": boolean,
  "content_type": "coupon" | "collab_ad" | "recommendation" | "organic",
  "brand": string,
  "coupon": string or null,
  "url": string or null,
  "Description": string,
  "category": "fashion" | "beauty" | "home" | "food" | "tech" | "kids" | "travel" | "other"
}
"""


# ============== HELPER FUNCTIONS ==============
def extract_text_with_tesseract(image_url: str) -> str:
    """Download image and extract text using Tesseract OCR"""
    if not TESSERACT_AVAILABLE:
        return ""
    
    try:
        # Download image
        cookies = {}
        if IG_SESSIONID:
            cookies["sessionid"] = IG_SESSIONID
        
        r = requests.get(
            image_url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.instagram.com/",
            },
            cookies=cookies
        )
        
        if r.status_code != 200:
            print(f"⚠️ Image download failed: {r.status_code}")
            return ""
        
        # Open image with PIL
        image = Image.open(io.BytesIO(r.content))
        
        # Run OCR (supports Hebrew + English)
        text = pytesseract.image_to_string(image, lang='heb+eng')
        
        return text.strip()
    
    except Exception as e:
        print(f"❌ Tesseract OCR error: {e}")
        return ""


def call_gpt4o_mini_text_only(row: Dict[str, Any], ocr_text: str, context_str: str = "") -> Optional[Dict[str, Any]]:
    """Call GPT-4o-mini with text-only input (much cheaper than vision)"""
    
    # Build text payload
    caption = row.get("caption_text", "")
    stickers = row.get("stickers", [])
    sticker_texts = [s.get("text", "") for s in stickers if isinstance(s, dict)]
    urls = row.get("urls", [])
    
    user_message = f"""
Context from previous stories:
{context_str}

Current Story Data:
- Username: {row.get('username', 'unknown')}
- Caption: {caption}
- OCR Text (from image): {ocr_text}
- Sticker Texts: {', '.join(sticker_texts)}
- URLs: {', '.join(urls)}

Analyze this story and return JSON.
"""
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.1,
        "max_tokens": 500
    }
    
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        r = requests.post(OPENAI_API_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        
        response = r.json()
        content = response["choices"][0]["message"]["content"]
        
        # Extract JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        
        return json.loads(content)
    
    except Exception as e:
        print(f"❌ GPT-4o-mini error: {e}")
        return None


# ============== MAIN LOGIC ==============
def process_story_tesseract(row: Dict[str, Any], context_str: str = "") -> Optional[Dict[str, Any]]:
    """Process a single story using Tesseract + GPT-4o-mini"""
    
    # Step 1: Extract text from image using Tesseract
    image_url = row.get("image_url")
    ocr_text = ""
    
    if image_url:
        print(f"🔍 Running Tesseract OCR on: {image_url[:50]}...")
        ocr_text = extract_text_with_tesseract(image_url)
        print(f"📝 OCR extracted: {len(ocr_text)} chars")
        if ocr_text:
            print(f"Preview: {ocr_text[:100]}...")
    
    # Step 2: Analyze with GPT-4o-mini (text-only, cheap!)
    result = call_gpt4o_mini_text_only(row, ocr_text, context_str)
    
    return result


def main():
    """Test the Tesseract approach on a few stories"""
    
    if not TESSERACT_AVAILABLE:
        print("❌ Tesseract not available. Install with: pip install pytesseract pillow")
        print("Also install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki")
        return
    
    print("🚀 Testing Tesseract OCR + GPT-4o-mini approach...")
    print("=" * 60)
    
    # Fetch a few test stories from Supabase
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    
    url = f"{SUPABASE_REST}/{RAW_TABLE}"
    params = {
        "select": "*",
        "processing->>status": "neq.done",
        "limit": "3",
        "order": "taken_at_iso.desc"
    }
    
    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200:
        print(f"❌ Failed to fetch stories: {r.status_code}")
        return
    
    stories = r.json()
    print(f"📊 Fetched {len(stories)} test stories\n")
    
    for i, story in enumerate(stories, 1):
        print(f"\n{'='*60}")
        print(f"Story {i}/{len(stories)}: {story.get('media_id')}")
        print(f"User: {story.get('username', 'unknown')}")
        print(f"{'='*60}")
        
        result = process_story_tesseract(story, context_str="")
        
        if result:
            print("\n✅ Analysis Result:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("\n❌ Analysis failed")
    
    print("\n" + "="*60)
    print("✅ Test complete!")
    print("\nCost comparison:")
    print("- Current (GPT-4o vision): ~$0.00765 per story")
    print("- This approach (Tesseract + GPT-4o-mini): ~$0.00015 per story")
    print("- Savings: ~98%")


if __name__ == "__main__":
    main()
