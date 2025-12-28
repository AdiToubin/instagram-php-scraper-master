#!/usr/bin/env python3
"""
run_daily_stories.py - Daily Instagram Stories Automation

This script automates fetching Instagram stories for all influencers in a JSON file.
It includes randomization features to avoid bot detection:
- Random delays between requests (30-120 seconds)
- Random order of influencers
- Homepage simulation every 3 influencers
- Comprehensive logging and error handling

Usage:
    python run_daily_stories.py [--file FILE] [--min-delay SECONDS] [--max-delay SECONDS] [--dry-run]

Examples:
    python run_daily_stories.py
    python run_daily_stories.py --file user_ids_2025-11-29.json
    python run_daily_stories.py --min-delay 20 --max-delay 90 --dry-run
"""

import os
import sys
import json
import time
import random
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
import glob

# ============== CONFIGURATION ==============

DEFAULT_MIN_DELAY = 5  # seconds between influencers
DEFAULT_MAX_DELAY = 15
HOMEPAGE_INTERVAL = 3  # simulate homepage visit every N influencers
HOMEPAGE_MIN_DELAY = 5  # shorter delay for homepage
HOMEPAGE_MAX_DELAY = 15
PHP_SCRIPT = "stories_with_stickers.php"
PHP_EXECUTABLE = os.getenv("PHP_PATH", "C:\\php\\php.exe")

# ============== LOGGING ==============

class Logger:
    """Simple logger with timestamps and colors"""
    
    def __init__(self, log_file: Optional[str] = None):
        self.log_file = log_file
        if log_file:
            # Create logs directory if needed
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
    
    def _write(self, level: str, message: str, color: str = ""):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] {message}"
        
        # Console output
        print(log_line)
        
        # File output
        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
    
    def info(self, message: str):
        self._write("INFO", message)
    
    def success(self, message: str):
        self._write("SUCCESS", message)
    
    def warning(self, message: str):
        self._write("WARNING", message)
    
    def error(self, message: str):
        self._write("ERROR", message)
    
    def debug(self, message: str):
        self._write("DEBUG", message)

# ============== HELPER FUNCTIONS ==============

def find_latest_json_file(directory: str = ".") -> Optional[str]:
    """Find the most recent user_ids_*.json file"""
    pattern = os.path.join(directory, "user_ids_*.json")
    files = glob.glob(pattern)
    
    if not files:
        return None
    
    # Sort by modification time, newest first
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def load_influencers(json_file: str, logger: Logger) -> List[Dict[str, Any]]:
    """Load influencer data from JSON file"""
    logger.info(f"Loading influencers from: {json_file}")
    
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        results = data.get("results", [])
        logger.success(f"Loaded {len(results)} influencers")
        return results
    
    except FileNotFoundError:
        logger.error(f"File not found: {json_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading file: {e}")
        sys.exit(1)

def simulate_homepage_visit(logger: Logger, dry_run: bool = False) -> bool:
    """
    Simulate a visit to Instagram homepage to appear more human-like.
    In a real implementation, this would make a simple GET request to Instagram.
    For now, we just log and wait.
    """
    logger.info("🏠 Simulating homepage visit...")
    
    if dry_run:
        logger.debug("(Dry run - skipping actual homepage request)")
        return True
    
    # In a real implementation, you could make a simple request here
    # For example: requests.get("https://www.instagram.com/", headers=...)
    # For now, we just simulate the delay
    
    delay = random.uniform(HOMEPAGE_MIN_DELAY, HOMEPAGE_MAX_DELAY)
    logger.debug(f"Homepage visit delay: {delay:.1f}s")
    time.sleep(delay)
    
    return True

def run_stories_script(user_id: str, username: str, logger: Logger, dry_run: bool = False) -> bool:
    """
    Run the stories_with_stickers.php script for a specific user.
    Returns True if successful, False otherwise.
    """
    logger.info(f"📸 Fetching stories for @{username} (ID: {user_id})")
    
    if dry_run:
        logger.debug("(Dry run - skipping actual PHP execution)")
        return True
    
    try:
        # Build command
        cmd = [PHP_EXECUTABLE, PHP_SCRIPT, user_id]
        
        # Run with timeout
        # Fix for Windows encoding issues with Hebrew characters
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',  # Force UTF-8 encoding
            errors='replace',  # Replace problematic characters instead of crashing
            timeout=120,  # 2 minute timeout
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        
        if result.returncode == 0:
            logger.success(f"✓ Successfully fetched stories for @{username}")
            return True
        else:
            logger.error(f"✗ Failed for @{username}: {result.stderr[:200]}")
            return False
    
    except subprocess.TimeoutExpired:
        logger.error(f"✗ Timeout for @{username} (exceeded 120s)")
        return False
    except FileNotFoundError:
        logger.error(f"PHP executable not found: {PHP_EXECUTABLE}")
        return False
    except Exception as e:
        logger.error(f"✗ Error for @{username}: {str(e)[:200]}")
        return False

def random_delay(min_seconds: int, max_seconds: int, logger: Logger):
    """Wait for a random amount of time"""
    delay = random.uniform(min_seconds, max_seconds)
    logger.debug(f"⏳ Waiting {delay:.1f}s before next action...")
    time.sleep(delay)

# ============== MAIN AUTOMATION ==============

def run_automation(
    json_file: str,
    min_delay: int,
    max_delay: int,
    dry_run: bool,
    logger: Logger
):
    """Main automation logic"""
    
    logger.info("=" * 60)
    logger.info("🚀 Starting Daily Stories Automation")
    logger.info("=" * 60)
    
    # Load influencers
    influencers = load_influencers(json_file, logger)
    
    if not influencers:
        logger.error("No influencers found in JSON file")
        return
    
    # Filter out private accounts
    public_influencers = [
        inf for inf in influencers 
        if not inf.get("is_private", False)
    ]
    
    private_count = len(influencers) - len(public_influencers)
    if private_count > 0:
        logger.info(f"Skipping {private_count} private accounts")
    
    # Shuffle for randomization
    random.shuffle(public_influencers)
    logger.info(f"Processing {len(public_influencers)} public accounts in random order")
    
    # Statistics
    stats = {
        "total": len(public_influencers),
        "success": 0,
        "failed": 0,
        "skipped": private_count,
        "homepage_visits": 0
    }
    
    start_time = time.time()
    
    # Process each influencer
    for idx, influencer in enumerate(public_influencers, 1):
        user_id = influencer.get("user_id")
        username = influencer.get("username", "unknown")
        
        if not user_id:
            logger.warning(f"Skipping {username} - no user_id")
            stats["skipped"] += 1
            continue
        
        # Progress indicator
        logger.info(f"\n--- Progress: {idx}/{stats['total']} ---")
        
        # Run the stories script
        success = run_stories_script(user_id, username, logger, dry_run)
        
        if success:
            stats["success"] += 1
        else:
            stats["failed"] += 1
        
        # Homepage simulation every N influencers
        if idx % HOMEPAGE_INTERVAL == 0 and idx < stats["total"]:
            simulate_homepage_visit(logger, dry_run)
            stats["homepage_visits"] += 1
        
        # Random delay before next influencer (unless it's the last one)
        if idx < stats["total"]:
            random_delay(min_delay, max_delay, logger)
    
    # Final summary
    elapsed_time = time.time() - start_time
    
    logger.info("\n" + "=" * 60)
    logger.info("📊 AUTOMATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total processed:    {stats['total']}")
    logger.info(f"✓ Successful:       {stats['success']}")
    logger.info(f"✗ Failed:           {stats['failed']}")
    logger.info(f"⊘ Skipped:          {stats['skipped']}")
    logger.info(f"🏠 Homepage visits:  {stats['homepage_visits']}")
    logger.info(f"⏱ Total time:       {elapsed_time/60:.1f} minutes")
    logger.info("=" * 60)
    
    if stats["failed"] > 0:
        logger.warning(f"⚠️  {stats['failed']} influencers failed - check logs for details")

# ============== CLI ==============

def main():
    parser = argparse.ArgumentParser(
        description="Automate Instagram stories fetching with randomization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_daily_stories.py
  python run_daily_stories.py --file user_ids_2025-11-29.json
  python run_daily_stories.py --min-delay 20 --max-delay 90
  python run_daily_stories.py --dry-run
        """
    )
    
    parser.add_argument(
        "--file",
        type=str,
        help="Path to JSON file with user IDs (default: auto-detect latest)"
    )
    
    parser.add_argument(
        "--min-delay",
        type=int,
        default=DEFAULT_MIN_DELAY,
        help=f"Minimum delay between requests in seconds (default: {DEFAULT_MIN_DELAY})"
    )
    
    parser.add_argument(
        "--max-delay",
        type=int,
        default=DEFAULT_MAX_DELAY,
        help=f"Maximum delay between requests in seconds (default: {DEFAULT_MAX_DELAY})"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test run without actually calling PHP script"
    )
    
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to log file (default: logs/stories_YYYYMMDD_HHMMSS.log)"
    )
    
    args = parser.parse_args()
    
    # Determine JSON file
    if args.file:
        json_file = args.file
    else:
        json_file = find_latest_json_file()
        if not json_file:
            print("❌ No user_ids_*.json file found. Please specify --file")
            sys.exit(1)
    
    # Determine log file
    if args.log_file:
        log_file = args.log_file
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"logs/stories_{timestamp}.log"
    
    # Initialize logger
    logger = Logger(log_file)
    
    # Validate delays
    if args.min_delay < 0 or args.max_delay < 0:
        logger.error("Delays must be positive")
        sys.exit(1)
    
    if args.min_delay > args.max_delay:
        logger.error("min-delay cannot be greater than max-delay")
        sys.exit(1)
    
    # Log configuration
    logger.info(f"Configuration:")
    logger.info(f"  JSON file:     {json_file}")
    logger.info(f"  Delay range:   {args.min_delay}-{args.max_delay} seconds")
    logger.info(f"  Homepage freq: Every {HOMEPAGE_INTERVAL} influencers")
    logger.info(f"  Dry run:       {args.dry_run}")
    logger.info(f"  Log file:      {log_file}")
    logger.info("")
    
    # Run automation
    try:
        run_automation(
            json_file=json_file,
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            dry_run=args.dry_run,
            logger=logger
        )
    except KeyboardInterrupt:
        logger.warning("\n⚠️  Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
