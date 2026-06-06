# backend/scraper/extract.py
import re
import json
import httpx
from bs4 import BeautifulSoup
from dataclasses import dataclass, field

@dataclass
class ScrapedPart:
    ps_number: str
    mpn: str
    name: str
    description: str
    price_cents: int
    in_stock: bool
    image_url: str | None
    rating: float | None
    review_count: int
    appliance_type: str          # 'refrigerator' | 'dishwasher'
    video_url: str | None
    symptoms: list[str] = field(default_factory=list)
    alt_numbers: list[str] = field(default_factory=list)
    install_steps: list[dict] = field(default_factory=list)

@dataclass
class ScrapedModel:
    model_number: str
    brand: str
    appliance_type: str
    part_ps_numbers: list[str] = field(default_factory=list)

def parse_product_page(html: str, appliance_type: str) -> ScrapedPart:
    soup = BeautifulSoup(html, "html.parser")

    # Try JSON-LD first
    json_ld = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "Product":
                        json_ld = item
                        break
            elif data.get("@type") == "Product":
                json_ld = data
                break
        except Exception:
            continue

    # Part number parsing
    ps_number_el = soup.select_one(".ps-partnum")
    ps_number = ps_number_el.text.replace("PartSelect #:", "").strip() if ps_number_el else ""
    if not ps_number:
        # Fallback regex
        match = re.search(r"PS\d+", html)
        if match:
            ps_number = match.group()

    # Manufacturer part number
    mpn_el = soup.select_one(".mfr-partnum")
    mpn = mpn_el.text.replace("Manufacturer #:", "").strip() if mpn_el else ""

    # Name
    name_el = soup.select_one("h1.title, .title")
    name = name_el.text.strip() if name_el else "PartSelect Part"

    # Description
    desc_el = soup.select_one(".description-text, .description")
    description = desc_el.text.strip() if desc_el else ""

    # Price
    price_text = None
    if json_ld and "offers" in json_ld:
        offers = json_ld["offers"]
        if isinstance(offers, list):
            price_text = offers[0].get("price")
        else:
            price_text = offers.get("price")
    
    if not price_text:
        price_el = soup.select_one(".price")
        price_text = price_el.text.replace("$", "").strip() if price_el else "0"

    try:
        price_cents = round(float(price_text) * 100)
    except Exception:
        price_cents = 0

    # Stock
    in_stock = True
    if json_ld and "offers" in json_ld:
        offers = json_ld["offers"]
        availability = ""
        if isinstance(offers, list):
            availability = offers[0].get("availability", "")
        else:
            availability = offers.get("availability", "")
        if "OutOfStock" in availability:
            in_stock = False

    stock_el = soup.select_one(".stock-status")
    if stock_el and "Out of Stock" in stock_el.text:
        in_stock = False

    # Symptoms
    symptoms = [li.text.strip() for li in soup.select(".symptoms-list li, .fixes-symptoms li, [data-symptom]")]

    # Alternate part numbers
    alt_numbers = [mpn] if mpn else []
    for el in soup.select(".cross-ref-list span, .replaces-list span, .alt-partnum"):
        val = el.text.strip()
        if val and val not in alt_numbers:
            alt_numbers.append(val)

    # Image URL
    image_url = None
    if json_ld and "image" in json_ld:
        image_url = json_ld["image"]
    if not image_url:
        img_el = soup.select_one(".part-img, img[src*='/parts/']")
        if img_el:
            image_url = img_el.get("src")

    # Rating
    rating = None
    review_count = 0
    if json_ld and "aggregateRating" in json_ld:
        ar = json_ld["aggregateRating"]
        try:
            rating = float(ar.get("ratingValue"))
            review_count = int(ar.get("reviewCount", 0))
        except Exception:
            pass

    # Video URL
    video_el = soup.select_one("a[href*='youtube.com'], a[href*='youtu.be']")
    video_url = video_el.get("href") if video_el else None

    return ScrapedPart(
        ps_number=ps_number,
        mpn=mpn,
        name=name,
        description=description,
        price_cents=price_cents,
        in_stock=in_stock,
        image_url=image_url,
        rating=rating,
        review_count=review_count,
        appliance_type=appliance_type,
        video_url=video_url,
        symptoms=symptoms,
        alt_numbers=alt_numbers,
        install_steps=[]
    )

def parse_model_page(html: str) -> ScrapedModel:
    soup = BeautifulSoup(html, "html.parser")
    model_number_el = soup.select_one(".model-number, [data-model-number]")
    model_number = model_number_el.text.strip() if model_number_el else ""

    title = (soup.select_one("h1") or soup.select_one("title")).text.lower()
    appliance_type = "dishwasher" if "dishwasher" in title else "refrigerator"

    ps_numbers = []
    # Find all PartSelect part numbers on page
    for el in soup.select("[data-ps-number], .part-link, a[href*='/parts/PS']"):
        ps = el.get("data-ps-number") or el.get("href")
        if ps:
            match = re.search(r"PS\d+", ps)
            if match:
                ps_numbers.append(match.group())
        else:
            match = re.search(r"PS\d+", el.text)
            if match:
                ps_numbers.append(match.group())

    return ScrapedModel(
        model_number=model_number,
        brand="Whirlpool",  # Default for demo
        appliance_type=appliance_type,
        part_ps_numbers=list(set(ps_numbers)),
    )

async def crawl_category(category_url: str, appliance_type: str) -> list[ScrapedPart]:
    """Crawl a category page, follow product links, parse each."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(category_url)
            soup = BeautifulSoup(resp.text, "html.parser")
            product_urls = [a["href"] for a in soup.select("a[href*='/PS']") if "/PS" in a.get("href", "")]
            parts = []
            for url in product_urls[:10]:   # cap to prevent huge requests
                full_url = f"https://www.partselect.com{url}" if url.startswith("/") else url
                resp = await client.get(full_url)
                parts.append(parse_product_page(resp.text, appliance_type))
            return parts
        except Exception as e:
            print(f"Error crawling category {category_url}: {e}")
            return []
