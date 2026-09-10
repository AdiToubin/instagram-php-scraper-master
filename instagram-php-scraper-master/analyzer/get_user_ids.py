#!/usr/bin/env python3
"""
get_user_ids.py - Python port of scraper/get_user_ids.php.

Converts a list of Instagram usernames to user_ids via the private
web_profile_info endpoint, and writes user_ids_<timestamp>.json to the
project root - the same location run_daily_stories.py's
find_latest_json_file() already reads from.

Usage:
    python get_user_ids.py username1 username2 ...
    python get_user_ids.py --file influencers.txt
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ig_scraper.client import MissingSessionError, fetch_user_profile, fetch_user_profile_html

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_usernames(args: argparse.Namespace) -> List[str]:
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"File not found: {args.file}", file=sys.stderr)
            sys.exit(3)
        lines = path.read_text(encoding="utf-8").splitlines()
        return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if args.usernames:
        return [u.strip() for u in args.usernames if u.strip()]
    print("Usage: python get_user_ids.py username1 username2 ...\n   or: python get_user_ids.py --file usernames.txt", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve Instagram usernames to user_ids")
    parser.add_argument("usernames", nargs="*", help="usernames to resolve")
    parser.add_argument("--file", help="path to a file with one username per line")
    args = parser.parse_args()

    usernames = load_usernames(args)
    print(f"Fetching user IDs for {len(usernames)} username(s)...\n")

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for username in usernames:
        print(f"Processing: @{username} ... ", end="")
        try:
            try:
                data = fetch_user_profile(username)
            except MissingSessionError:
                raise
            except Exception as api_error:
                print(f"[API failed: {api_error}] falling back to HTML scrape... ", end="")
                data = fetch_user_profile_html(username)
            user = (data.get("data") or {}).get("user") or {}
            user_id = user.get("id")
            if not user_id:
                print("ERROR (User ID not found)")
                errors.append({"username": username, "error": "User ID not found in response"})
                continue
            results.append({
                "username": username,
                "user_id": user_id,
                "full_name": user.get("full_name", ""),
                "is_private": bool(user.get("is_private", False)),
                "followers": ((user.get("edge_followed_by") or {}).get("count", 0)),
            })
            print(f"✓ ID: {user_id}")
        except MissingSessionError as e:
            print(f"ERROR ({e})")
            sys.exit(3)
        except Exception as e:  # noqa: BLE001 - mirrors PHP's catch-all + continue
            print(f"ERROR ({e})")
            errors.append({"username": username, "error": str(e)})

        time.sleep(0.5)  # mirrors get_user_ids.php's usleep(500000)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60 + "\n")

    if results:
        print(f"Successfully fetched {len(results)} user ID(s):\n")
        print(f"{'Username':<20} {'User ID':<15} {'Full Name':<30} Followers")
        print("-" * 100)
        for r in results:
            privacy = " \U0001F512" if r["is_private"] else ""
            print(f"@{r['username']:<19} {r['user_id']:<15} {r['full_name'][:28]:<30} {r['followers']:,}{privacy}")

        print("\n" + "-" * 60)
        print("User IDs only (for batch processing):")
        print("\n".join(str(r["user_id"]) for r in results))

        output_file = PROJECT_ROOT / f"user_ids_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
        output_file.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now().isoformat(),
                    "total_requested": len(usernames),
                    "total_found": len(results),
                    "total_errors": len(errors),
                    "results": results,
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n✓ Results saved to: {output_file}")

    if errors:
        print("\n" + "=" * 60)
        print(f"ERRORS ({len(errors)})")
        print("=" * 60 + "\n")
        for err in errors:
            print(f"• @{err['username']}: {err['error']}")

    print()


if __name__ == "__main__":
    main()
