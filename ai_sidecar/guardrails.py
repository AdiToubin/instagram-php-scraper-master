import re

def is_safe_code(code: str) -> bool:
    """
    Validates if a string is a safe/valid coupon code candidate.
    Refusal criteria:
    - Phone numbers
    - Time ranges
    - Prices
    - Invalid characters
    - Must have at least ONE digit (per user requirement)
    """
    if not code: 
        return False
    
    # Normalize
    normalized = code.strip().upper()
    
    # Length Rule (3-20)
    if len(normalized) < 3 or len(normalized) > 20: 
        return False
        
    # Charset Rule (A-Z, 0-9, - and _)
    if not re.match(r'^[A-Z0-9_\-]+$', normalized):
        return False
        
    # Content Rule: Must contain at least one digit
    if not re.search(r'\d', normalized):
        return False
    
    # Block Phone Numbers (054-0000000, 0540000000)
    # Checks for 05X + 7 digits loose match
    if re.search(r'05\d[\-]?\d{7}', normalized): 
        return False
    
    # Block Times (10:00, 14:30-16:00, 9:00-11:00)
    if re.search(r'\d{1,2}:\d{2}', normalized): 
        return False
    
    # Block Prices (100NIS, 50$, ₪599, 50 USD)
    # Currency prefix (₪599, $50)
    if re.search(r'^(nis|usd|\$|₪)\s?\d+', normalized, re.IGNORECASE): 
        return False
    # Currency suffix (100NIS, 50$)
    if re.search(r'\d+\s?(nis|usd|\$|₪)', normalized, re.IGNORECASE): 
        return False
        
    return True
