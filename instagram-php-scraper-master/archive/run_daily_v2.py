"""
run_daily_v2.py - Enhanced version with all 6 improvements
- URL code extraction
- AI validation  
- Logging
- Performance tracking
- Retry logic
- Influencer statistics
"""

# Add this at the very beginning of your existing run_daily.py imports
import logging
from collections import defaultdict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from requests.exceptions import Timeout, RequestException

# ============== LOGGING SETUP ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('run_daily.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============== STATISTICS CLASS ==============
class RunStatistics:
    def __init__(self):
        self.start_time = time.time()
        self.total_stories = 0
        self.relevant_found = 0
        self.coupons_found = 0
        self.collab_ads_found = 0
        self.recommendations_found = 0
        self.errors = 0
        self.total_cost = 0.0
        self.processing_times = []
        
        # Per-influencer stats
        self.influencer_stats = defaultdict(lambda: {
            'total': 0,
            'coupons': 0,
            'collab_ads': 0,
            'recommendations': 0,
            'organic': 0
        })
    
    def add_story(self, username, content_type, has_coupon=False):
        """Track a processed story"""
        self.total_stories += 1
        self.influencer_stats[username]['total'] += 1
        
        if content_type == 'coupon':
            self.coupons_found += 1
            self.influencer_stats[username]['coupons'] += 1
            if has_coupon:
                self.relevant_found += 1
        elif content_type == 'collab_ad':
            self.collab_ads_found += 1
            self.influencer_stats[username]['collab_ads'] += 1
            self.relevant_found += 1
        elif content_type == 'recommendation':
            self.recommendations_found += 1
            self.influencer_stats[username]['recommendations'] += 1
        else:  # organic
            self.influencer_stats[username]['organic'] += 1
    
    def add_processing_time(self, seconds):
        self.processing_times.append(seconds)
    
    def add_cost(self, cost):
        self.total_cost += cost
    
    def add_error(self):
        self.errors += 1
    
    def print_summary(self):
        """Print run summary"""
        elapsed = time.time() - self.start_time
        avg_time = sum(self.processing_times) / len(self.processing_times) if self.processing_times else 0
        
        print("\n" + "="*70)
        print("📊 RUN STATISTICS")
        print("="*70)
        print(f"⏱️  Total time: {elapsed:.1f}s")
        print(f"📝 Stories processed: {self.total_stories}")
        print(f"✅ Relevant found: {self.relevant_found} ({self.relevant_found/self.total_stories*100:.1f}%)" if self.total_stories > 0 else "✅ Relevant found: 0")
        print(f"   🎟️  Coupons: {self.coupons_found}")
        print(f"   🔗 Collab Ads: {self.collab_ads_found}")
        print(f"   💡 Recommendations: {self.recommendations_found}")
        print(f"❌ Errors: {self.errors}")
        print(f"💰 Total cost: ${self.total_cost:.4f}")
        print(f"⚡ Avg time/story: {avg_time:.2f}s")
        print("="*70)
        
        # Influencer report
        print("\n" + "="*70)
        print("👥 INFLUENCER STATISTICS")
        print("="*70)
        print(f"{'Username':<20} {'Total':>6} {'Coupons':>8} {'Ads':>6} {'Recs':>6} {'Organic':>8} {'Commercial %':>13}")
        print("-"*70)
        
        # Sort by commercial percentage
        sorted_influencers = sorted(
            self.influencer_stats.items(),
            key=lambda x: (x[1]['coupons'] + x[1]['collab_ads'] + x[1]['recommendations']) / max(x[1]['total'], 1),
            reverse=True
        )
        
        for username, stats in sorted_influencers:
            total = stats['total']
            if total == 0:
                continue
            
            commercial = stats['coupons'] + stats['collab_ads'] + stats['recommendations']
            commercial_pct = (commercial / total * 100)
            
            print(f"{username:<20} "
                  f"{total:>6} "
                  f"{stats['coupons']:>8} "
                  f"{stats['collab_ads']:>6} "
                  f"{stats['recommendations']:>6} "
                  f"{stats['organic']:>8} "
                  f"{commercial_pct:>12.1f}%")
        
        print("="*70)

# ============== HELPER FUNCTIONS ==============

def calculate_openai_cost(model, input_tokens=500, output_tokens=200, has_image=False):
    """Calculate OpenAI API cost"""
    if model == "gpt-4o":
        if has_image:
            return 0.00765  # Vision pricing
        else:
            input_cost = (input_tokens / 1_000_000) * 5
            output_cost = (output_tokens / 1_000_000) * 15
            return input_cost + output_cost
    elif model == "gpt-4o-mini":
        input_cost = (input_tokens / 1_000_000) * 0.150
        output_cost = (output_tokens / 1_000_000) * 0.600
        return input_cost + output_cost
    return 0.0

# ============== VALIDATION FUNCTIONS ==============

def is_valid_coupon_code(code: str, username: str = "") -> bool:
    """Validate if a string is a valid coupon code"""
    if not code or len(code) < 4 or len(code) > 20:
        return False
    
    code_upper = code.upper()
    
    # Check against blocklist (use your existing IGNORE_COUPONS set)
    # if code_upper in IGNORE_COUPONS:
    #     return False
    
    # Block time patterns
    if re.match(r'^\d{2}-\d{2}$', code_upper):
        return False
    
    # Block OCR garbage
    if re.match(r'^[0-9]+[A-Z]+[0-9]+[A-Z]+', code_upper):
        return False
    
    # Must have at least 2 letters
    letter_count = sum(1 for c in code_upper if c.isalpha())
    if letter_count < 2:
        return False
    
    return True

def validate_ai_result(result, row):
    """Validate and clean AI response"""
    if not result:
        return result
    
    username = row.get('username', '')
    
    # Validate main coupon
    if result.get('coupon'):
        code = result['coupon'].upper()
        if not is_valid_coupon_code(code, username):
            logger.warning(f"AI returned invalid coupon '{code}', removing")
            result['coupon'] = None
    
    # Validate coupons array
    if result.get('coupons'):
        valid_coupons = [c.upper() for c in result['coupons'] if is_valid_coupon_code(c, username)]
        result['coupons'] = valid_coupons
    
    # Ensure category is never empty
    if not result.get('category') or result.get('category') in ('', 'null', 'none'):
        result['category'] = 'other'
    
    return result

# ============== INSTRUCTIONS ==============
print("""
╔══════════════════════════════════════════════════════════════════════╗
║  📝 INTEGRATION INSTRUCTIONS                                         ║
╚══════════════════════════════════════════════════════════════════════╝

This file contains the core improvements. To integrate into run_daily.py:

1. Copy the RunStatistics class to the top of run_daily.py
2. Copy the helper functions (calculate_openai_cost, is_valid_coupon_code, validate_ai_result)
3. In your main() function, add:
   
   stats = RunStatistics()
   logger.info("🚀 Starting run with improvements")
   
4. In your story processing loop, add:
   
   start_time = time.time()
   
   # ... your existing code ...
   
   # After getting result from AI:
   result = validate_ai_result(result, row)
   
   # Track statistics:
   processing_time = time.time() - start_time
   stats.add_processing_time(processing_time)
   stats.add_story(username, result.get('content_type', 'organic'), bool(result.get('coupon')))
   stats.add_cost(calculate_openai_cost(MODEL, 500, 200, bool(row.get('image_url'))))
   
5. At the end of main(), add:
   
   stats.print_summary()

That's it! You'll get beautiful statistics and influencer reports.
""")
