from typing import Dict, Any, List, Optional
import json

def save_to_memory(
    supabase_client, 
    media_id: str, 
    user_id: str,
    context: str, 
    vector: List[float], 
    result: Dict[str, Any], 
    is_ground_truth: bool,
    verification_source: str = "sidecar"
):
    """
    Saves the analyzed story to stories_memory.
    Uses UPSERT based on media_id to handle re-runs gracefully.
    """
    data = {
        "media_id": media_id,
        "user_id": user_id,
        "context_text": context,
        "embedding": vector,
        "verdict": result.get('verdict', 'organic'),
        "extracted_data": result,
        "confidence": result.get('confidence', 0.0),
        "is_ground_truth": is_ground_truth,
        "verification_source": verification_source
    }
    
    # Supabase UPSERT
    # on_conflict="media_id" ensures we update if it exists
    response = supabase_client.table("stories_memory").upsert(
        data, 
        on_conflict="media_id"
    ).execute()
    
    return response

def get_memory_stats(supabase_client):
    """Helper to check memory health"""
    res = supabase_client.table("stories_memory").select("verdict", count="exact").execute()
    return res.count
