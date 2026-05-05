import os
import json
import logging
import openai
from typing import Dict, Any, List, Optional
from .guardrails import is_safe_code

# Configure Logger
logger = logging.getLogger(__name__)

# Constants
EMBEDDING_MODEL = "text-embedding-3-small"
LLM_MODEL = "gpt-4o-mini"
SYSTEM_PROMPT = """You are a precise Instagram Coupon Analyzer for Israeli influencers.
Your goal is to detect VALID coupon codes and affiliate links.
Output strictly JSON.
"""

def build_context(story_obj: Dict[str, Any]) -> str:
    """
    Aggregates all story signals into a single rich text context.
    Handles sticker lists of strings or dicts.
    """
    # Caption
    raw_caption = story_obj.get("caption_text") or ""
    
    # Stickers
    stickers_raw = story_obj.get("stickers") or []
    sticker_texts = []
    for s in stickers_raw:
        if isinstance(s, dict):
            t = s.get("text")
            if t: sticker_texts.append(str(t))
        elif isinstance(s, str):
            sticker_texts.append(s)
            
    sticker_blob = " / ".join(sticker_texts)
    
    # OCR
    ocr_text = story_obj.get("ocr_text") or ""
    
    # URL
    urls = story_obj.get("urls") or []
    # Filter out internal instagram urls
    filtered_urls = []
    for u in urls:
        if isinstance(u, dict):
             # Try to find a 'url' or 'href' key, or just dump it
             u = u.get("url") or u.get("href") or str(u)
        
        if isinstance(u, str) and "instagram.com" not in u:
            filtered_urls.append(u)

    url_text = " ".join(filtered_urls)
    
    # Flatten newlines
    context = f"""
    CAPTION: {raw_caption}
    STICKERS: {sticker_blob}
    OCR: {ocr_text}
    URLS: {url_text}
    """.strip()
    
    return " ".join(context.split())

def get_embedding(text: str) -> List[float]:
    """Generates embedding for the text context."""
    if not text:
        return [0.0] * 1536 # Return zero vector if empty
        
    try:
        res = openai.embeddings.create(input=text, model=EMBEDDING_MODEL)
        return res.data[0].embedding
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return [0.0] * 1536

def retrieve_hybrid(supabase_client, embedding: List[float], user_id: str) -> List[Dict[str, Any]]:
    """
    ✅ IMPROVED: Retrieves 6 positive examples (user + global) and 2 negatives.
    ✅ ACTIVE LEARNING: Includes user corrections as high-priority examples!
    """
    try:
        # 1. Try to get 6 examples from this specific user
        user_examples = supabase_client.rpc("hybrid_search", {
            "query_vec": embedding,
            "match_threshold": 0.60,  # ⬇️ Lowered from 0.70 for better recall
            "match_count": 6,         # ⬆️ Increased from 4
            "filter_user_id": user_id
        }).execute().data
        
        # 2. If not enough user examples, supplement with global examples
        if len(user_examples) < 4:
            logger.info(f"Only {len(user_examples)} user examples found, adding global examples")
            global_examples = supabase_client.rpc("hybrid_search", {
                "query_vec": embedding,
                "match_threshold": 0.65,
                "match_count": 6 - len(user_examples)
                # No filter_user_id = global search
            }).execute().data
            examples = user_examples + global_examples
        else:
            examples = user_examples[:6]
        
        # 3. Get 2 Negative (Organic) Examples to prevent hallucination
        negatives = supabase_client.rpc("hybrid_search", {
            "query_vec": embedding,
            "match_threshold": 0.55,  # ⬇️ Lowered from 0.60
            "match_count": 2,         # ⬆️ Increased from 1
            "filter_verdict": "organic"
        }).execute().data
        
        # ✅ 4. ACTIVE LEARNING: Add user corrections as high-priority examples!
        correction_examples = []
        try:
            corrections = supabase_client.table('user_corrections') \
                .select('*') \
                .eq('user_id', user_id) \
                .order('corrected_at', desc=True) \
                .limit(3) \
                .execute().data
            
            # Convert corrections to example format
            for corr in corrections:
                # Build context from correction
                context = corr.get('story_context', '')
                if not context:
                    # Fallback: build minimal context
                    context = f"Brand: {corr.get('correct_brand', 'unknown')}, Code: {corr.get('correct_code', 'none')}"
                
                correction_examples.append({
                    'context_text': context,
                    'verdict': corr['correct_verdict'],
                    'extracted_data': {
                        'code': corr.get('correct_code'),
                        'brand': corr.get('correct_brand')
                    },
                    'similarity': 1.0,  # Maximum priority!
                    'source': 'USER_CORRECTION'  # Special marker
                })
            
            if correction_examples:
                logger.info(f"📚 Added {len(correction_examples)} user corrections as examples")
        except Exception as e:
            logger.debug(f"No corrections available: {e}")
        
        # Prioritize: corrections first, then examples, then negatives
        all_examples = correction_examples + examples + negatives
        
        logger.info(f"Retrieved {len(correction_examples)} corrections + {len(examples)} positive + {len(negatives)} negative examples")
        return all_examples
        
    except Exception as e:
        logger.error(f"Retrieval failed: {e}")
        return []

def enforce_evidence(result_code: str, result_ev: str, story_obj: Dict[str, Any]) -> bool:
    """
    Validation: If LLM claims code came from 'X', check if 'X' actually contains it.
    """
    if not result_ev or not result_code: 
        return True # Cannot check, assume passed or irrelevant
    
    target_text = ""
    ev = result_ev.lower()
    code_check = result_code.lower()
    
    if "sticker" in ev:
        stickers = story_obj.get("stickers") or []
        for s in stickers:
            if isinstance(s, dict): target_text += (s.get("text") or "")
            elif isinstance(s, str): target_text += s
    
    if "caption" in ev:
        target_text += (story_obj.get("caption_text") or "")
        
    if "ocr" in ev:
        target_text += (story_obj.get("ocr_text") or "")
        
    if "url" in ev:
         target_text += str(story_obj.get("urls") or "")

    # If the code (or reasonable substring) is not in the text source, reject.
    if code_check not in target_text.lower():
         logger.warning(f"Hallucination detected: Code '{result_code}' not found in '{ev}'")
         return False 
         
    return True

def analyze_story_sidecar(story_obj: Dict[str, Any], supabase_client) -> Dict[str, Any]:
    """
    Main Sidecar Entry Point.
    """
    # 1. Context
    context = build_context(story_obj)
    
    # 2. Embedding
    embedding = get_embedding(context)
    
    # 3. Retrieval
    examples = retrieve_hybrid(supabase_client, embedding, story_obj.get("user_id"))
    
    # 4. Prompt Construction
    example_text = ""
    for ex in examples:
        code_info = ex['extracted_data'].get('code', 'None')
        example_text += f"- Input: {ex['context_text'][:200]}...\n  Result: {ex['verdict']} (Code: {code_info})\n"
        
    # ✅ IMPROVED: Extract URLs and hashtags for brand hints
    story_urls = story_obj.get('urls', [])
    story_hashtags = story_obj.get('hashtags', [])
    
    final_prompt = f"""
    LEARN from these past examples:
    {example_text}
    
    ANALYZE this current story:
    {context}
    
    Return JSON format:
    {{
        "verdict": "coupon|collab_ad|recommendation|organic",
        "code": "string or null",
        "link": "target url or null",
        "brand": "brand name or null",
        "description": "Short summary in HEBREW",
        "category": "Fashion|Beauty|Home|Kids|Food|Travel|Tech|Other",
        "confidence": 0.0-1.0,
        "evidence": "caption|ocr|sticker|url"
    }}
    
    RULES:
    - verdict: 'coupon' if there is a VALID textual code. 'collab_ad' if link only.
    - code: ONLY textual codes (e.g. SAVE20). Must be explicit.
    - link: Affiliate links or bio links.
    - evidence: Where did you find the code/link?
    - description: Write a concise summary of the VISUAL/TEXTUAL content in HEBREW (עברית). Do NOT purely say "no code"; describe what is actually happening (e.g., "Family dinner", "Selfie", "Product unpacking").
    - category: Pick the best fit from the list.
    - If it's just a phone number or time, verdict is ORGANIC.
    
    ✅ BRAND EXTRACTION (CRITICAL):
    1. Check URLs first: domain = brand
       Example: "addictonline.co.il/CORIN" → brand: "addict"
    2. Check hashtags: #brandname
       Example: "#reserved" → brand: "reserved"
    3. Check sticker text: often has "קוד של [brand]"
       Example: "קוד של קולנטה" → brand: "kolenta"
    4. If multiple brands, pick the MAIN one (with code/link)
    5. Normalize: lowercase, remove spaces, no special chars
    
    Current story URLs: {story_urls}
    Current story hashtags: {story_hashtags}
    """
    
    # 5. LLM Call with Vision Support ✅
    try:
        # Build message content (text + optional image)
        user_content = [{"type": "text", "text": final_prompt}]
        
        # ✅ VISION ANALYSIS: Add image if available
        image_url = story_obj.get("image_url")
        if image_url:
            # Check if it's a valid external URL (not Instagram CDN)
            if "instagram.com" not in image_url and "fbcdn.net" not in image_url:
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": image_url,
                        "detail": "low"  # Cost-effective
                    }
                })
                logger.info(f"✅ Added image to analysis: {image_url[:50]}...")
            else:
                logger.debug("Skipping Instagram CDN image (use data URI if needed)")
        
        response = openai.chat.completions.create(
            model=LLM_MODEL,  # gpt-4o-mini supports vision!
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        result = json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"LLM Call failed: {e}")
        return {"verdict": "organic", "confidence": 0.0, "error": str(e)}

    # 6. Guardrails
    extracted_code = result.get('code')
    
    if extracted_code:
        # A. Regex/Rule Safety
        if not is_safe_code(extracted_code):
            logger.info(f"Guardrail rejected code: {extracted_code}")
            result['verdict'] = 'organic'
            result['code'] = None
            
        # B. Evidence Enforcement
        elif not enforce_evidence(extracted_code, result.get('evidence'), story_obj):
             result['verdict'] = 'organic'
             result['code'] = None

    # Return Result + Vector (for storage)
    return {
        "result": result,
        "vector": embedding,
        "context": context
    }
