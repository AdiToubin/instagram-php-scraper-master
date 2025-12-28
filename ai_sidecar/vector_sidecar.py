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
SYSTEM_PROMPT = """You are a precise Instagram Coupon Analyzer. Output strict JSON.
Your goal is to detect VALID coupon codes and affiliate links.
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
    url_text = " ".join([u for u in urls if "instagram.com" not in u])
    
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
    Retrieves positive examples and one negative example.
    """
    try:
        # 1. Get 4 Positive/Relevant Examples
        positives = supabase_client.rpc("hybrid_search", {
            "query_vec": embedding,
            "match_threshold": 0.70,
            "match_count": 4,
            "filter_user_id": user_id
        }).execute().data
        
        # 2. Get 1 Negative (Organic) Example to prevent hallucination
        negatives = supabase_client.rpc("hybrid_search", {
            "query_vec": embedding,
            "match_threshold": 0.60,
            "match_count": 1,
            "filter_verdict": "organic"
        }).execute().data
        
        return positives + negatives
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
        "confidence": 0.0-1.0,
        "evidence": "caption|ocr|sticker|url"
    }}
    
    RULES:
    - verdict: 'coupon' if there is a VALID textual code. 'collab_ad' if link only.
    - code: ONLY textual codes (e.g. SAVE20). Must be explicit.
    - link: Affiliate links or bio links.
    - evidence: Where did you find the code/link?
    - If it's just a phone number or time, verdict is ORGANIC.
    """
    
    # 5. LLM Call
    try:
        response = openai.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": final_prompt}
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
