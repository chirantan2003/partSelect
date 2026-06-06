# backend/scraper/utils.py
"""Shared utilities for all PartSelect scrapers."""

import re
import os
import json
import time
import random
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from backend.scraper.config import (
    BASE_URL, TIMEOUT,
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX,
    RETRY_ATTEMPTS, RETRY_BACKOFF_BASE,
    BRANDS, REFRIGERATOR_KEYWORDS, DISHWASHER_KEYWORDS,
)

logger = logging.getLogger("partselect_scraper")


# ─── HTTP Session ────────────────────────────────────────────────────────

def create_session() -> cffi_requests.Session:
    """
    Create a curl_cffi Session that impersonates Chrome browser fingerprint.
    This bypasses Cloudflare / TLS-based bot detection that blocks plain requests.
    """
    return cffi_requests.Session(impersonate="chrome")


def fetch_with_retry(session: cffi_requests.Session, url: str) -> str | None:
    """
    Fetch a URL with exponential backoff retry logic.

    Returns HTML text on success, or None on total failure.
    - 404  → None immediately (page gone)
    - 403/429 → retry with backoff (rate limit)
    - Timeout/Error → retry with backoff
    """
    for attempt in range(RETRY_ATTEMPTS):
        try:
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            resp = session.get(url, timeout=TIMEOUT)

            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 404:
                logger.warning(f"404: {url}")
                return None
            elif resp.status_code in (403, 429):
                wait = RETRY_BACKOFF_BASE ** attempt
                logger.warning(f"Rate limited ({resp.status_code}), retry {attempt+1}/{RETRY_ATTEMPTS} in {wait}s — {url}")
                time.sleep(wait)
            else:
                logger.warning(f"HTTP {resp.status_code}: {url}")
                time.sleep(RETRY_BACKOFF_BASE ** attempt)

        except Exception as e:
            wait = RETRY_BACKOFF_BASE ** attempt
            logger.warning(f"Error (retry {attempt+1}/{RETRY_ATTEMPTS} in {wait}s): {e} — {url}")
            time.sleep(wait)

    logger.error(f"All retries exhausted: {url}")
    return None


# ─── URL Helpers ─────────────────────────────────────────────────────────

def make_absolute_url(url: str) -> str:
    """Convert a relative URL to absolute PartSelect URL."""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL + url
    return BASE_URL + "/" + url


def is_valid_image_url(url: str) -> bool:
    """Check that a URL points to a real image file."""
    return bool(url) and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))


# ─── PS Number Extraction ─────────────────────────────────────────────────

def extract_ps_number(text: str) -> str | None:
    """Extract first PS number (e.g. 'PS11752778') from URL or text."""
    match = re.search(r"PS\d+", text)
    return match.group() if match else None


def extract_all_ps_numbers(text: str) -> list[str]:
    """Extract all unique PS numbers from text."""
    return list(set(re.findall(r"PS\d+", text)))


def extract_part_numbers(text: str) -> dict:
    """Extract PS numbers and likely manufacturer part numbers from text."""
    ps = list(set(re.findall(r"PS\d+", text)))
    mpn = list(set(re.findall(r"\b[A-Z0-9][A-Z0-9\-]{4,14}\b", text)))
    mpn = [m for m in mpn if not m.startswith("PS")]
    return {"ps_numbers": ps, "mpn_numbers": mpn}


# ─── Text Processing ─────────────────────────────────────────────────────

def strip_html(html: str) -> str:
    """Convert HTML to clean plain text."""
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


def extract_first_paragraph(html: str) -> str:
    """Get the first non-empty paragraph from HTML."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if text and len(text) > 30:
            return text
    return ""


def truncate_text(text: str, max_length: int = 100_000) -> str:
    """Limit text to max_length characters."""
    return text[:max_length] + "..." if len(text) > max_length else text


def validate_content_length(text: str, min_length: int = 200, max_length: int = 100_000) -> bool:
    """Check that content is within acceptable length bounds."""
    return min_length <= len(text) <= max_length


# ─── Price Extraction ────────────────────────────────────────────────────

def extract_price_cents(text: str) -> int:
    """Parse '$52.67' or '52.67' → 5267 (integer cents)."""
    if not text:
        return 0
    cleaned = re.sub(r"[^\d.]", "", text)
    try:
        return round(float(cleaned) * 100)
    except (ValueError, TypeError):
        return 0


# ─── Brand & Appliance Detection ─────────────────────────────────────────

def extract_brand(text: str) -> str | None:
    """Detect the first matching brand name from text."""
    text_lower = text.lower()
    for brand, variations in BRANDS.items():
        for v in variations:
            if v in text_lower:
                return brand
    return None


def extract_multiple_brands(text: str) -> list[str]:
    """Extract all brand names mentioned in text."""
    text_lower = text.lower()
    found = []
    for brand, variations in BRANDS.items():
        for v in variations:
            if v in text_lower:
                found.append(brand)
                break
    return found


def classify_appliance_type(text: str) -> str | None:
    """Classify text as 'refrigerator' or 'dishwasher' by keyword scoring."""
    text_lower = text.lower()
    fridge = sum(1 for kw in REFRIGERATOR_KEYWORDS if kw in text_lower)
    dish = sum(1 for kw in DISHWASHER_KEYWORDS if kw in text_lower)
    if fridge > dish:
        return "refrigerator"
    elif dish > fridge:
        return "dishwasher"
    return None


# ─── Video Extraction ────────────────────────────────────────────────────

def extract_youtube_id(element) -> str | None:
    """Extract YouTube video ID from a BeautifulSoup element."""
    for attr in ("data-video-id", "data-youtube-id", "data-yt-id"):
        val = element.get(attr, "")
        if val and len(val) == 11:
            return val
    src = element.get("src", "") or element.get("href", "")
    match = re.search(r"(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([a-zA-Z0-9_-]{11})", src)
    return match.group(1) if match else None


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_thumbnail(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"


# ─── Data Validation ─────────────────────────────────────────────────────

def validate_document(doc: dict, required_fields: list[str]) -> bool:
    """Return True only if all required fields are present and non-empty."""
    return all(doc.get(f) for f in required_fields)


def deduplicate_by_url(documents: list[dict]) -> list[dict]:
    """Remove duplicates by source_url field."""
    seen, unique = set(), []
    for doc in documents:
        url = doc.get("source_url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(doc)
    return unique


def deduplicate_by_key(documents: list[dict], key: str) -> list[dict]:
    """Remove duplicates by an arbitrary key field."""
    seen, unique = set(), []
    for doc in documents:
        val = doc.get(key)
        if val and val not in seen:
            seen.add(val)
            unique.append(doc)
    return unique


# ─── File Operations ─────────────────────────────────────────────────────

def get_data_dir() -> str:
    """Return the data/raw directory path, creating it if needed."""
    path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "raw")
    )
    os.makedirs(path, exist_ok=True)
    return path


def file_exists(filename: str) -> bool:
    return os.path.exists(os.path.join(get_data_dir(), filename))


def save_json(data: dict, filename: str):
    """Save a dict as JSON to data/raw/<filename>."""
    path = os.path.join(get_data_dir(), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    size_kb = os.path.getsize(path) // 1024
    logger.info(f"Saved {path} ({size_kb} KB)")


def load_json(filename: str) -> dict | None:
    """Load a JSON file from data/raw/. Returns None if not found."""
    path = os.path.join(get_data_dir(), filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_checkpoint(documents: list, filename: str, metadata: dict | None = None):
    """Save a checkpoint JSON with documents and metadata."""
    save_json(
        {
            "metadata": {
                "checkpoint_date": get_timestamp(),
                "total_documents": len(documents),
                **(metadata or {}),
            },
            "documents": documents,
        },
        filename,
    )


def load_checkpoint(filename: str) -> list:
    """Load documents from a checkpoint file. Returns empty list if not found."""
    data = load_json(filename)
    return data.get("documents", []) if data else []


# ─── Misc ────────────────────────────────────────────────────────────────

def generate_document_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index:04d}"


def get_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def print_progress(current: int, total: int, label: str = ""):
    pct = (current / total * 100) if total > 0 else 0
    print(f"  [{current}/{total}] {pct:.1f}%  {label}        ", end="\r", flush=True)


def setup_logging(name: str = "partselect_scraper", level: int = logging.INFO) -> logging.Logger:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(name)
