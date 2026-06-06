# backend/scraper/config.py
"""Centralized configuration for all PartSelect scrapers."""

# ─── Network ────────────────────────────────────────────────────────────
BASE_URL = "https://www.partselect.com"

REQUEST_DELAY_MIN = 2.0    # Minimum seconds between requests
REQUEST_DELAY_MAX = 4.0    # Maximum seconds between requests
RETRY_ATTEMPTS = 5         # Max retries on failure
RETRY_BACKOFF_BASE = 3     # Exponential backoff base (3^n seconds)
TIMEOUT = 30               # HTTP timeout in seconds

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# ─── Scraper Limits ─────────────────────────────────────────────────────
MAX_PAGES_PER_CATEGORY = 0  # 0 = no limit (scrape all pages)
CHECKPOINT_INTERVAL = 50    # Save progress every N parts
MAX_CONTENT_LENGTH = 100_000
MIN_CONTENT_LENGTH = 200

# ─── Appliance Types ────────────────────────────────────────────────────
APPLIANCE_TYPES = ["Refrigerator", "Dishwasher"]

# ─── Brands (43 brands) ─────────────────────────────────────────────────
# Canonical name -> list of lowercase variations for text matching
BRANDS = {
    "Admiral": ["admiral"],
    "Amana": ["amana"],
    "Beko": ["beko"],
    "Blomberg": ["blomberg"],
    "Bosch": ["bosch"],
    "Caloric": ["caloric"],
    "Crosley": ["crosley"],
    "Dacor": ["dacor"],
    "Danby": ["danby"],
    "Electrolux": ["electrolux"],
    "Estate": ["estate"],
    "Fisher & Paykel": ["fisher & paykel", "fisher and paykel", "fisher paykel"],
    "Frigidaire": ["frigidaire"],
    "Gaggenau": ["gaggenau"],
    "General Electric": ["ge", "general electric", "g.e."],
    "Gibson": ["gibson"],
    "Gladiator": ["gladiator"],
    "Haier": ["haier"],
    "Hardwick": ["hardwick"],
    "Hotpoint": ["hotpoint"],
    "Ikea": ["ikea"],
    "Inglis": ["inglis"],
    "Jenn-Air": ["jenn-air", "jennair", "jenn air"],
    "Kelvinator": ["kelvinator"],
    "Kenmore": ["kenmore", "kenmore elite", "kenmore pro"],
    "KitchenAid": ["kitchenaid", "kitchen aid"],
    "LG": ["lg", "l.g."],
    "Litton": ["litton"],
    "Magic Chef": ["magic chef"],
    "Maytag": ["maytag"],
    "Miele": ["miele"],
    "Modern Maid": ["modern maid"],
    "Monogram": ["monogram"],
    "Norge": ["norge"],
    "Roper": ["roper"],
    "Samsung": ["samsung"],
    "Speed Queen": ["speed queen"],
    "Sub-Zero": ["sub-zero", "sub zero", "subzero"],
    "Tappan": ["tappan"],
    "Thermador": ["thermador"],
    "Viking": ["viking"],
    "White-Westinghouse": ["white-westinghouse", "white westinghouse"],
    "Whirlpool": ["whirlpool"],
    "Wolf": ["wolf"],
}

# ─── URLs ────────────────────────────────────────────────────────────────
# Parts catalog (facet search — confirmed working)
FACET_SEARCH_URL = BASE_URL + "/facetsearch/?brand={brand}&modeltype={modeltype}"
FACET_SEARCH_PAGED_URL = FACET_SEARCH_URL + "&pagenum={pagenum}"

# Repair symptom pages — discovered format: /Repair/{Appliance}/{Slug}/
REPAIR_INDEX_URL = BASE_URL + "/Repair/{appliance_type}/"
REPAIR_SYMPTOM_URL = BASE_URL + "/Repair/{appliance_type}/{slug}/"

# Blog topics
BLOG_URL_TEMPLATE = BASE_URL + "/blog/topics/{topic}/"

# ─── Repair Symptoms (discovered by scraping index page) ─────────────────
# Format: (slug, display_name)
REFRIGERATOR_SYMPTOMS = [
    ("Noisy",                    "Noisy"),
    ("Leaking",                  "Leaking"),
    ("Will-Not-Start",           "Will Not Start"),
    ("Not-Making-Ice",           "Ice Maker Not Making Ice"),
    ("Refrigerator-Too-Warm",    "Fridge Too Warm"),
    ("Not-Dispensing-Water",     "Not Dispensing Water"),
    ("Refrigerator-Freezer-Too-Warm", "Fridge and Freezer Too Warm"),
    ("Door-Sweating",            "Door Sweating"),
    ("Light-Not-Working",        "Light Not Working"),
    ("Refrigerator-Too-Cold",    "Fridge Too Cold"),
    ("Freezer-Too-Cold",         "Freezer Too Cold"),
    # Note: "Runs-Too-Long" returns HTTP 500 on partselect.com — removed
]

DISHWASHER_SYMPTOMS = [
    ("Noisy",                    "Noisy"),
    ("Leaking",                  "Leaking"),
    ("Will-Not-Start",           "Will Not Start"),
    ("Door-Latch-Failure",       "Door Latch Failure"),
    ("Not-Cleaning-Properly",    "Not Cleaning Dishes"),
    ("Not-Draining",             "Not Draining"),
    ("Will-Not-Fill-Water",      "Won't Fill With Water"),
    ("Will-Not-Dispense-Detergent", "Won't Dispense Detergent"),
    ("Not-Drying-Dishes",        "Not Drying Dishes"),
]

# ─── Blog Topics ─────────────────────────────────────────────────────────
BLOG_TOPICS = ["repair", "error-codes", "how-to-guides", "testing", "use-and-care"]

# ─── CSS Selectors — Listing Page (facet search) ──────────────────────────
# Confirmed against live site 2026-06-06
LISTING_SELECTORS = {
    "card":        "div.smart-search__parts__part",      # each part card
    "name_link":   "a.bold.text-md.text-black.mb-1",     # part name (also has href with PS#)
    "image":       "img",                                 # first img in card
    # Price and stock are in the card text: split by "|" gives tokens like "$", "84.45", "In Stock"
}

# ─── CSS Selectors — Detail Page ─────────────────────────────────────────
# Confirmed against live site 2026-06-06
DETAIL_SELECTORS = {
    "title":         "h1",
    "price":         ".pd__price",                        # e.g. "$84.45"
    "description":   ".pd__description",                  # product description div
    "stock":         "div[class*='repair']",              # contains "Easy"/"Medium"/"Hard"
    "rating_value":  "meta[itemprop='ratingValue']",
    "rating_count":  "meta[itemprop='reviewCount']",
    "symptoms":      "[class*='symptom']",
}

# ─── CSS Selectors — Repair Symptom Page ─────────────────────────────────
REPAIR_SELECTORS = {
    "title":         "h1",
    "symptom_links": "a[href*='/Repair/']",               # links to individual symptom pages
    "part_links":    "a[href*='/PS']",                    # PS part links
    "video":         "[data-video-id], iframe[src*='youtube']",
    "steps":         "ol li, .repair-steps li, [class*='step'] li",
    "intro_para":    "main p, article p, .repair-content p",
}

# ─── CSS Selectors — Blog Page ────────────────────────────────────────────
BLOG_SELECTORS = {
    "article_links": "a[href*='/blog/']",
    "title":         "h1",
    "content":       "article, .blog-content, .article-content, .post-body, main",
    "image":         "article img, .blog-content img, main img",
    "older_link":    "a[rel='next']",
}

# ─── Appliance Keywords ──────────────────────────────────────────────────
REFRIGERATOR_KEYWORDS = [
    "refrigerator", "fridge", "freezer", "ice maker", "ice machine",
    "defrost", "crisper", "compressor", "cold", "cooling",
]
DISHWASHER_KEYWORDS = [
    "dishwasher", "dish washer", "spray arm", "rinse aid",
    "detergent dispenser", "drain pump", "wash cycle",
]
