#!/usr/bin/env python3
"""
Influencer Performance Report Generator
========================================
מייצר דוח מפורט לפי משפיענית:
- כמות סטוריז כוללת
- כמה רלוונטיים (קופונים + לינקים + המלצות)
- כמה לא רלוונטיים
- פירוט לפי סוג תוכן
"""

import os
import json
import requests
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# Supabase config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")

SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def load_username_map():
    """טוען את המיפוי user_id -> username"""
    import glob
    files = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'user_ids_*.json'))
    if not files:
        return {}
    
    latest = max(files, key=os.path.getctime)
    with open(latest, "r", encoding="utf-8") as f:
        data = json.load(f)
        mapping = {}
        for u in data.get("results", []):
            uid = str(u.get("user_id", ""))
            uname = u.get("username", "")
            if uid and uname:
                mapping[uid] = uname
        return mapping


def fetch_all_stories():
    """שולף את כל הסטוריז מהדאטה-בייס"""
    url = f"{SUPABASE_REST}/story_raw"
    params = {
        "select": "user_id,media_id,processing",
        "limit": "10000",
    }
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def fetch_relevant_stories():
    """שולף קופונים ולינקים"""
    url = f"{SUPABASE_REST}/relevant_story"
    params = {
        "select": "user_id,media_id,url",
        "limit": "10000",
    }
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def fetch_recommendations():
    """שולף המלצות"""
    url = f"{SUPABASE_REST}/story_recommendations"
    params = {
        "select": "user_id,media_id",
        "limit": "10000",
    }
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def generate_report():
    """מייצר את הדוח"""
    print("📊 מייצר דוח משפיעניות...\n")
    
    # טוען נתונים
    username_map = load_username_map()
    all_stories = fetch_all_stories()
    relevant = fetch_relevant_stories()
    recommendations = fetch_recommendations()
    
    # מבנה נתונים לפי user_id
    stats = defaultdict(lambda: {
        "total": 0,
        "coupons": 0,
        "links": 0,
        "recommendations": 0,
        "organic": 0,
        "not_processed": 0,
    })
    
    # ספירת כל הסטוריז
    for story in all_stories:
        uid = str(story.get("user_id", "unknown"))
        stats[uid]["total"] += 1
        
        # בדיקת סטטוס עיבוד
        processing = story.get("processing")
        if not processing or not isinstance(processing, dict):
            stats[uid]["not_processed"] += 1
        elif processing.get("status") == "non_relevant":
            stats[uid]["organic"] += 1
    
    # ספירת קופונים ולינקים
    for story in relevant:
        uid = str(story.get("user_id", "unknown"))
        if story.get("url"):
            stats[uid]["links"] += 1
        else:
            stats[uid]["coupons"] += 1
    
    # ספירת המלצות
    for story in recommendations:
        uid = str(story.get("user_id", "unknown"))
        stats[uid]["recommendations"] += 1
    
    # מיון לפי כמות תוכן רלוונטי (יורד)
    sorted_users = sorted(
        stats.items(),
        key=lambda x: x[1]["coupons"] + x[1]["links"] + x[1]["recommendations"],
        reverse=True
    )
    
    # הדפסת הדוח
    print("=" * 100)
    print(f"{'משפיענית':<25} {'סה\"כ':<8} {'קופונים':<10} {'לינקים':<10} {'המלצות':<10} {'אורגני':<10} {'% רלוונטי':<12}")
    print("=" * 100)
    
    for uid, data in sorted_users:
        username = username_map.get(uid, f"user_{uid}")
        total = data["total"]
        coupons = data["coupons"]
        links = data["links"]
        recs = data["recommendations"]
        organic = data["organic"]
        
        relevant_count = coupons + links + recs
        if total > 0:
            relevant_pct = (relevant_count / total) * 100
        else:
            relevant_pct = 0
        
        # צבע לפי אחוז רלוונטיות
        if relevant_pct >= 50:
            indicator = "🟢"
        elif relevant_pct >= 20:
            indicator = "🟡"
        else:
            indicator = "🔴"
        
        print(f"{indicator} {username:<23} {total:<8} {coupons:<10} {links:<10} {recs:<10} {organic:<10} {relevant_pct:>6.1f}%")
    
    print("=" * 100)
    
    # סיכום כללי
    total_stories = sum(d["total"] for d in stats.values())
    total_coupons = sum(d["coupons"] for d in stats.values())
    total_links = sum(d["links"] for d in stats.values())
    total_recs = sum(d["recommendations"] for d in stats.values())
    total_organic = sum(d["organic"] for d in stats.values())
    
    print(f"\n📈 סיכום כללי:")
    print(f"   סה\"כ סטוריז: {total_stories}")
    print(f"   קופונים: {total_coupons}")
    print(f"   לינקים: {total_links}")
    print(f"   המלצות: {total_recs}")
    print(f"   אורגני: {total_organic}")
    print(f"   % רלוונטי כולל: {((total_coupons + total_links + total_recs) / total_stories * 100):.1f}%")
    
    # המלצות
    print(f"\n💡 המלצות:")
    low_performers = [
        (username_map.get(uid, f"user_{uid}"), data)
        for uid, data in stats.items()
        if data["total"] >= 10 and (data["coupons"] + data["links"] + data["recommendations"]) / data["total"] < 0.1
    ]
    
    if low_performers:
        print(f"   🔴 משפיעניות עם פחות מ-10% תוכן רלוונטי (שווה לשקול להסיר):")
        for username, data in low_performers[:5]:
            print(f"      - {username}: {data['total']} סטוריז, רק {data['coupons'] + data['links'] + data['recommendations']} רלוונטיים")
    else:
        print(f"   ✅ כל המשפיעניות מייצרות תוכן רלוונטי!")


if __name__ == "__main__":
    try:
        generate_report()
    except Exception as e:
        print(f"❌ שגיאה: {e}")
        import traceback
        traceback.print_exc()
