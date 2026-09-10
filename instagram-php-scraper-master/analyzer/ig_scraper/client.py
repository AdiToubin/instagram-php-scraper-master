"""
Instagram private-API HTTP client.

Python port of the Guzzle clients in scraper/stories_with_stickers.php and
scraper/get_user_ids.php. Talks directly to Instagram's private web API using
session cookies from .env - same headers, same endpoints, same behavior -
so the raw JSON this returns is a drop-in replacement for what the PHP
scraper used to fetch.
"""

import json
import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv

load_dotenv()

IG_SESSIONID = os.getenv("IG_SESSIONID")
IG_CSRF = os.getenv("IG_CSRF")
IG_DS_USER_ID = os.getenv("IG_DS_USER_ID")
IG_UA = os.getenv(
    "IG_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

BASE_URL = "https://www.instagram.com/"
IG_APP_ID = "936619743392459"


class MissingSessionError(RuntimeError):
    pass


def _require_session() -> None:
    if not (IG_SESSIONID and IG_CSRF and IG_DS_USER_ID):
        raise MissingSessionError("Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID")


def _headers(accept_encoding: str) -> Dict[str, str]:
    return {
        "User-Agent": IG_UA,
        "Referer": BASE_URL,
        "Origin": "https://www.instagram.com",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": accept_encoding,
        "X-Requested-With": "XMLHttpRequest",
        "X-IG-App-ID": IG_APP_ID,
        "X-CSRFToken": IG_CSRF or "",
        "Cookie": f"csrftoken={IG_CSRF}; sessionid={IG_SESSIONID}; ds_user_id={IG_DS_USER_ID};",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }


def fetch_stories(user_id: str) -> Dict[str, Any]:
    """Raw reels_media response for one user_id (same call as stories_with_stickers.php:280)."""
    _require_session()
    resp = requests.post(
        BASE_URL + "api/v1/feed/reels_media/",
        headers=_headers("gzip, deflate, br"),
        data={"user_ids": json.dumps([str(user_id)])},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} response:\n{resp.text[:500]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Bad JSON\n{resp.text[:500]}")
    return data


def fetch_user_profile(username: str) -> Dict[str, Any]:
    """Raw web_profile_info response for one username (same call as get_user_ids.php:119)."""
    _require_session()
    resp = requests.get(
        BASE_URL + "api/v1/users/web_profile_info/",
        params={"username": username},
        headers=_headers("gzip, deflate"),
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} for @{username}")
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid JSON response for @{username}")
    return data
