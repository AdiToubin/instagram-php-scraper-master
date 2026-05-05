# 🔧 URL Code Extraction & AI Validation
# Add this after the extract_coupons_from_urls function in run_daily.py

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.exceptions import Timeout, RequestException

# ============== IMPROVED URL CODE EXTRACTION ==============
def extract_and_validate_url_codes(urls: List[str], username: str = "") -> List[Dict[str, str]]:
    """
    Extract potential coupon codes from URL paths and validate them.
    Returns list of validated codes with metadata.
    """
    codes = []
    username_lower = username.lower()
    
    for url in urls:
        try:
            parsed = urlparse(url)
            path = parsed.path
            
            # Split path by common separators
            path_parts = re.split(r'[/_\-]', path)
            
            for part in path_parts:
                if not part or len(part) < 4:
                    continue
                
                part_upper = part.upper()
                
                # Apply validation rules
                if not is_valid_coupon_code(part_upper, username_lower):
                    continue
                
                # Check if it matches username (likely influencer code)
                is_influencer_code = username_lower and (
                    part.lower() in username_lower or 
                    username_lower in part.lower()
                )
                
                codes.append({
                    'code': part_upper,
                    'source': 'url_path',
                    'url': url,
                    'is_influencer_code': is_influencer_code,
                    'snippet': f"URL: {url[:100]}"
                })
        
        except Exception as e:
            logger.warning(f"Failed to extract code from URL {url}: {e}")
            continue
    
    # Deduplicate
    seen = set()
    unique_codes = []
    for code_info in codes:
        code = code_info['code']
        if code not in seen:
            seen.add(code)
            unique_codes.append(code_info)
    
    return unique_codes


def is_valid_coupon_code(code: str, username: str = "") -> bool:
    """
    Validate if a string is a valid coupon code.
    Returns True if valid, False otherwise.
    """
    # 1. Must be uppercase at this point
    if not code.isupper():
        return False
    
    # 2. Check against blocklist
    if code in IGNORE_COUPONS:
        return False
    
    # 3. Block time patterns (00-22, 30-15, etc.)
    if re.match(r'^\d{2}-\d{2}$', code):
        return False
    
    # 4. Block OCR garbage patterns
    if re.match(r'^[0-9]+[A-Z]+[0-9]+[A-Z]+', code):
        return False
    
    # 5. Must have at least ONE letter
    if not re.search(r'[A-Z]', code):
        return False
    
    # 6. Must have at least 2 letters (not just numbers)
    letter_count = sum(1 for c in code if c.isalpha())
    if letter_count < 2:
        return False
    
    # 7. Block if it's all digits with dashes
    if code.replace("-", "").isdigit():
        return False
    
    # 8. Length check (4-20 chars)
    if len(code) < 4 or len(code) > 20:
        return False
    
    return True


def validate_ai_result(result: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and clean AI response.
    Fixes common mistakes and applies business rules.
    """
    if not result:
        return result
    
    username = row.get('username', '')
    
    # 1. Validate main coupon field
    if result.get('coupon'):
        code = result['coupon'].upper()
        if not is_valid_coupon_code(code, username):
            logger.warning(f"AI returned invalid coupon '{code}', removing")
            result['coupon'] = None
    
    # 2. Validate coupons array
    if result.get('coupons'):
        valid_coupons = []
        for code in result['coupons']:
            code_upper = str(code).upper()
            if is_valid_coupon_code(code_upper, username):
                valid_coupons.append(code_upper)
            else:
                logger.warning(f"Filtered invalid coupon from array: '{code}'")
        result['coupons'] = valid_coupons
    
    # 3. Validate coupon_items
    if result.get('coupon_items'):
        valid_items = []
        for item in result['coupon_items']:
            code = item.get('code', '').upper()
            if is_valid_coupon_code(code, username):
                item['code'] = code
                valid_items.append(item)
            else:
                logger.warning(f"Filtered invalid coupon item: '{code}'")
        result['coupon_items'] = valid_items
    
    # 4. Consistency check: if content_type is collab_ad, remove coupons
    if result.get('content_type') == 'collab_ad' and not result.get('coupon'):
        result['coupons'] = []
        result['coupon_items'] = []
    
    # 5. Ensure category is never empty
    if not result.get('category') or result.get('category') in ('', 'null', 'none'):
        result['category'] = 'other'
    
    # 6. Ensure brand is never "General" or empty
    if result.get('brand') in ('General', 'Unknown', 'Brand', '', None):
        result['brand'] = 'unknown'
    
    return result


# ============== RETRY LOGIC WITH EXPONENTIAL BACKOFF ==============
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((Timeout, RequestException)),
    reraise=True
)
def call_openai_with_retry(payload: Dict[str, Any]) -> requests.Response:
    """
    Call OpenAI API with automatic retry on network errors.
    Uses exponential backoff: 2s, 4s, 8s
    """
    logger.debug("Calling OpenAI API...")
    response = requests.post(
        OPENAI_URL,
        headers=OA_HEADERS,
        json=payload,
        timeout=90
    )
    
    # Raise for 5xx errors (will trigger retry)
    if response.status_code >= 500:
        logger.warning(f"OpenAI server error {response.status_code}, will retry...")
        response.raise_for_status()
    
    # Raise for 429 (rate limit)
    if response.status_code == 429:
        logger.warning("Rate limited, will retry...")
        response.raise_for_status()
    
    return response


# ============== ENHANCED STORY PROCESSING ==============
def process_story_with_improvements(row: Dict[str, Any], stats: RunStatistics) -> Optional[Dict[str, Any]]:
    """
    Process a single story with all improvements:
    1. Extract codes from URLs
    2. Call AI with retry logic
    3. Validate AI response
    4. Track statistics
    """
    media_id = row.get('media_id')
    username = row.get('username', 'unknown')
    
    start_time = time.time()
    
    try:
        # Step 1: Extract codes from URLs
        urls = extract_all_urls(row)
        url_codes = extract_and_validate_url_codes(urls, username)
        
        if url_codes:
            logger.info(f"Found {len(url_codes)} codes in URLs: {[c['code'] for c in url_codes]}")
        
        # Step 2: Build payload with URL codes hint
        user_blob = build_user_payload(row)
        user_blob['url_extracted_codes'] = [c['code'] for c in url_codes]
        
        # Step 3: Call OpenAI with retry
        payload = build_openai_payload(user_blob, row)
        response = call_openai_with_retry(payload)
        
        # Step 4: Parse response
        result = parse_openai_response(response)
        
        # Step 5: Validate and clean result
        result = validate_ai_result(result, row)
        
        # Step 6: Merge URL codes if AI missed them
        if url_codes and not result.get('coupon'):
            # AI missed the code, use the one from URL
            result['coupon'] = url_codes[0]['code']
            result['coupons'] = [c['code'] for c in url_codes]
            result['coupon_items'] = url_codes
            result['is_relevant'] = True
            result['content_type'] = 'coupon'
            logger.info(f"AI missed URL code, added: {url_codes[0]['code']}")
        
        # Step 7: Track statistics
        processing_time = time.time() - start_time
        stats.add_processing_time(processing_time)
        stats.add_story(
            username,
            result.get('content_type', 'organic'),
            has_coupon=bool(result.get('coupon'))
        )
        
        # Estimate cost (simplified)
        has_image = bool(row.get('image_url'))
        cost = calculate_openai_cost(MODEL, 500, 200, has_image)
        stats.add_cost(cost)
        
        logger.info(f"✅ Processed {media_id} in {processing_time:.2f}s")
        return result
    
    except Exception as e:
        logger.error(f"❌ Failed to process {media_id}: {e}")
        stats.add_error()
        return None


# Helper functions (simplified versions - you'll need to adapt to your code)
def build_user_payload(row):
    """Build user payload for OpenAI"""
    # Your existing logic here
    pass

def build_openai_payload(user_blob, row):
    """Build OpenAI API payload"""
    # Your existing logic here
    pass

def parse_openai_response(response):
    """Parse OpenAI response"""
    # Your existing logic here
    pass
