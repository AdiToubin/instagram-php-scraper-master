# 📊 Influencer Statistics & Performance Tracking
# Add this to the top of run_daily.py after imports

import logging
from collections import defaultdict
from datetime import datetime
import time

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

# ============== STATISTICS TRACKING ==============
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
        """Track processing time for a story"""
        self.processing_times.append(seconds)
    
    def add_cost(self, cost):
        """Track API cost"""
        self.total_cost += cost
    
    def add_error(self):
        """Track an error"""
        self.errors += 1
    
    def get_influencer_report(self):
        """Generate per-influencer statistics"""
        report = []
        for username, stats in sorted(self.influencer_stats.items()):
            total = stats['total']
            if total == 0:
                continue
            
            commercial = stats['coupons'] + stats['collab_ads'] + stats['recommendations']
            commercial_pct = (commercial / total * 100) if total > 0 else 0
            
            report.append({
                'username': username,
                'total_stories': total,
                'coupons': stats['coupons'],
                'collab_ads': stats['collab_ads'],
                'recommendations': stats['recommendations'],
                'organic': stats['organic'],
                'commercial_count': commercial,
                'commercial_percentage': commercial_pct
            })
        
        # Sort by commercial percentage (most commercial first)
        report.sort(key=lambda x: x['commercial_percentage'], reverse=True)
        return report
    
    def print_summary(self):
        """Print run summary"""
        elapsed = time.time() - self.start_time
        avg_time = sum(self.processing_times) / len(self.processing_times) if self.processing_times else 0
        
        print("\n" + "="*70)
        print("📊 RUN STATISTICS")
        print("="*70)
        print(f"⏱️  Total time: {elapsed:.1f}s")
        print(f"📝 Stories processed: {self.total_stories}")
        print(f"✅ Relevant found: {self.relevant_found} ({self.relevant_found/self.total_stories*100:.1f}%)")
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
        
        for inf in self.get_influencer_report():
            print(f"{inf['username']:<20} "
                  f"{inf['total_stories']:>6} "
                  f"{inf['coupons']:>8} "
                  f"{inf['collab_ads']:>6} "
                  f"{inf['recommendations']:>6} "
                  f"{inf['organic']:>8} "
                  f"{inf['commercial_percentage']:>12.1f}%")
        
        print("="*70)

# ============== COST CALCULATION ==============
def calculate_openai_cost(model, input_tokens, output_tokens, has_image=False):
    """Calculate OpenAI API cost"""
    # GPT-4o pricing (as of Dec 2024)
    if model == "gpt-4o":
        if has_image:
            # Vision pricing: $0.00765 per image (low detail)
            return 0.00765
        else:
            # Text-only: $5/1M input, $15/1M output
            input_cost = (input_tokens / 1_000_000) * 5
            output_cost = (output_tokens / 1_000_000) * 15
            return input_cost + output_cost
    elif model == "gpt-4o-mini":
        # $0.150/1M input, $0.600/1M output
        input_cost = (input_tokens / 1_000_000) * 0.150
        output_cost = (output_tokens / 1_000_000) * 0.600
        return input_cost + output_cost
    return 0.0

# ============== DRY RUN SUPPORT ==============
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

def safe_upsert(table_name, payload, function):
    """Wrapper for DB operations with dry-run support"""
    if DRY_RUN:
        logger.info(f"🔍 DRY RUN - Would insert into {table_name}: {payload.get('media_id')}")
        return None
    else:
        return function(payload)
