# Daily Stories Automation

## Overview

`run_daily_stories.py` is an automated script that fetches Instagram stories for all influencers in your JSON file. It includes intelligent randomization to avoid bot detection.

## Features

✨ **Smart Randomization**
- Random delays between 30-120 seconds between each influencer
- Shuffles influencer order randomly each run
- Homepage simulation every 3 influencers (5-15 second delay)

🛡️ **Bot Detection Avoidance**
- Mimics human browsing patterns
- Simulates homepage visits
- Variable timing between requests

📊 **Comprehensive Logging**
- Real-time progress tracking
- Detailed logs saved to `logs/` directory
- Success/failure statistics

🔧 **Error Handling**
- Continues processing even if one influencer fails
- Automatic timeout protection (120s per request)
- Skips private accounts automatically

## Quick Start

### Basic Usage

```powershell
# Auto-detect latest JSON file and run
python run_daily_stories.py
```

### Advanced Usage

```powershell
# Specify a specific JSON file
python run_daily_stories.py --file user_ids_2025-11-29_192213.json

# Customize delay range (20-90 seconds)
python run_daily_stories.py --min-delay 20 --max-delay 90

# Test without actually running (dry run)
python run_daily_stories.py --dry-run

# Custom log file location
python run_daily_stories.py --log-file my_custom_log.txt
```

## Command-Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--file` | Path to JSON file with user IDs | Auto-detect latest |
| `--min-delay` | Minimum delay between requests (seconds) | 30 |
| `--max-delay` | Maximum delay between requests (seconds) | 120 |
| `--dry-run` | Test run without calling PHP script | False |
| `--log-file` | Path to log file | `logs/stories_YYYYMMDD_HHMMSS.log` |

## How It Works

1. **Load Influencers**: Reads the JSON file with influencer data
2. **Filter**: Removes private accounts automatically
3. **Shuffle**: Randomizes the order of influencers
4. **Process**: For each influencer:
   - Runs `stories_with_stickers.php` with their user_id
   - Waits 30-120 seconds (random)
   - Every 3 influencers: simulates homepage visit (5-15 seconds)
5. **Report**: Shows summary statistics

## Example Output

```
[2025-11-29 21:30:00] [INFO] ============================================================
[2025-11-29 21:30:00] [INFO] 🚀 Starting Daily Stories Automation
[2025-11-29 21:30:00] [INFO] ============================================================
[2025-11-29 21:30:00] [INFO] Loading influencers from: user_ids_2025-11-29_192213.json
[2025-11-29 21:30:00] [SUCCESS] Loaded 49 influencers
[2025-11-29 21:30:00] [INFO] Skipping 1 private accounts
[2025-11-29 21:30:00] [INFO] Processing 48 public accounts in random order

[2025-11-29 21:30:00] [INFO] --- Progress: 1/48 ---
[2025-11-29 21:30:00] [INFO] 📸 Fetching stories for @sivanazriel123 (ID: 1936447986)
[2025-11-29 21:30:05] [SUCCESS] ✓ Successfully fetched stories for @sivanazriel123
[2025-11-29 21:30:05] [DEBUG] ⏳ Waiting 67.3s before next action...

[2025-11-29 21:31:12] [INFO] --- Progress: 2/48 ---
[2025-11-29 21:31:12] [INFO] 📸 Fetching stories for @danagrotsky (ID: 29131423)
[2025-11-29 21:31:18] [SUCCESS] ✓ Successfully fetched stories for @danagrotsky
[2025-11-29 21:31:18] [DEBUG] ⏳ Waiting 45.8s before next action...

[2025-11-29 21:32:04] [INFO] --- Progress: 3/48 ---
[2025-11-29 21:32:04] [INFO] 📸 Fetching stories for @corringideon (ID: 212668164)
[2025-11-29 21:32:10] [SUCCESS] ✓ Successfully fetched stories for @corringideon
[2025-11-29 21:32:10] [INFO] 🏠 Simulating homepage visit...
[2025-11-29 21:32:10] [DEBUG] Homepage visit delay: 8.2s
[2025-11-29 21:32:18] [DEBUG] ⏳ Waiting 92.1s before next action...

...

[2025-11-29 23:15:42] [INFO] ============================================================
[2025-11-29 23:15:42] [INFO] 📊 AUTOMATION SUMMARY
[2025-11-29 23:15:42] [INFO] ============================================================
[2025-11-29 23:15:42] [INFO] Total processed:    48
[2025-11-29 23:15:42] [INFO] ✓ Successful:       46
[2025-11-29 23:15:42] [INFO] ✗ Failed:           2
[2025-11-29 23:15:42] [INFO] ⊘ Skipped:          1
[2025-11-29 23:15:42] [INFO] 🏠 Homepage visits:  16
[2025-11-29 23:15:42] [INFO] ⏱ Total time:       105.7 minutes
[2025-11-29 23:15:42] [INFO] ============================================================
```

## Scheduling Daily Runs

### Windows Task Scheduler

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger to daily at your preferred time
4. Action: Start a program
   - Program: `python`
   - Arguments: `C:\Users\efrat\instagram-php-scraper-master\run_daily_stories.py`
   - Start in: `C:\Users\efrat\instagram-php-scraper-master`

### Manual Daily Run

Just run this command once per day:

```powershell
cd C:\Users\efrat\instagram-php-scraper-master
python run_daily_stories.py
```

## Requirements

- Python 3.7+
- PHP with `stories_with_stickers.php` script
- Environment variables set (IG_SESSIONID, IG_CSRF, IG_DS_USER_ID)
- JSON file with influencer data

## Troubleshooting

**"No user_ids_*.json file found"**
- Make sure you have a JSON file in the directory
- Or specify the file explicitly with `--file`

**"PHP executable not found"**
- Set the `PHP_PATH` environment variable
- Or edit the script to point to your PHP location

**Script runs too fast/slow**
- Adjust `--min-delay` and `--max-delay` values
- Default is 30-120 seconds

## Notes

- The script automatically skips private accounts
- Logs are saved in the `logs/` directory
- Use `--dry-run` to test without making actual requests
- Homepage visits happen every 3 influencers to appear more human-like
