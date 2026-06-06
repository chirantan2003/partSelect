# backend/scraper/parts_scraper.py
"""
Parts catalog scraper for PartSelect.com.

Crawls the facet search system across all 43 brands × 2 appliance types,
extracting all parts with pricing, availability, and specifications.

Usage:
    python -m backend.scraper.parts_scraper                         # all brands
    python -m backend.scraper.parts_scraper --brands Whirlpool GE  # specific brands
    python -m backend.scraper.parts_scraper --max-pages 5          # limit pages per combo
    python -m backend.scraper.parts_scraper --resume               # resume from checkpoint

Output: data/raw/parts_raw.json
Runtime: ~30-45 minutes for all brands
"""

import re
import sys
import argparse
from bs4 import BeautifulSoup

from backend.scraper.config import (
    BASE_URL, BRANDS, APPLIANCE_TYPES,
    FACET_SEARCH_URL, FACET_SEARCH_PAGED_URL,
    LISTING_SELECTORS, CHECKPOINT_INTERVAL, MAX_PAGES_PER_CATEGORY,
)
from backend.scraper.utils import (
    create_session, fetch_with_retry, make_absolute_url,
    extract_ps_number, extract_price_cents,
    save_json, save_checkpoint, load_checkpoint, load_json,
    deduplicate_by_key, generate_document_id, get_timestamp,
    setup_logging,
)

logger = setup_logging("parts_scraper")


class PartsScraper:
    """Scrapes the PartSelect parts catalog using facet search navigation."""

    def __init__(self):
        self.session = create_session()
        self.total_scraped = 0
        self.failed_urls: list[str] = []
        self.documents: list[dict] = []
        self.brands_found: set[str] = set()
        self._seen_ps: set[str] = set()

    # ─── Public API ──────────────────────────────────────────────────

    def scrape_all_appliances(
        self,
        brands: list[str] | None = None,
        max_pages: int = 0,
        resume: bool = False,
    ) -> dict:
        """
        Scrape all appliance types × brands.

        Navigation: appliance type → brand → paginated results
        12 parts per page, with &pagenum= query parameter for pagination.
        """
        start_brand = None
        start_appliance_type = None
        start_page = 1

        if resume:
            checkpoint_data = load_json("parts_checkpoint.json")
            if checkpoint_data:
                self.documents = checkpoint_data.get("documents", [])
                self._seen_ps = {d["ps_number"] for d in self.documents if d.get("ps_number")}
                logger.info(f"Resumed from checkpoint: {len(self.documents)} parts")

                meta = checkpoint_data.get("metadata", {})
                start_brand = meta.get("brand")
                start_appliance_type = meta.get("appliance_type")
                start_page = meta.get("last_page", 1)
                if start_brand:
                    logger.info(f"Resuming search at: {start_brand} — {start_appliance_type} (starting page {start_page})")

        target_brands = brands or list(BRANDS.keys())
        total_combos = len(target_brands) * len(APPLIANCE_TYPES)
        combo_idx = 0

        skipping = False
        if resume and start_brand and start_appliance_type:
            if start_brand in target_brands:
                skipping = True

        for appliance_type in APPLIANCE_TYPES:
            for brand in target_brands:
                combo_idx += 1

                if skipping:
                    if brand == start_brand and appliance_type == start_appliance_type:
                        skipping = False
                        logger.info(
                            f"\n{'='*55}\n"
                            f"[{combo_idx}/{total_combos}] {brand} — {appliance_type} (Resuming from page {start_page})\n"
                            f"{'='*55}"
                        )
                        self._scrape_brand_type(brand, appliance_type, max_pages, start_page=start_page)
                        continue
                    else:
                        logger.info(f"Skipping already completed category: {brand} — {appliance_type}")
                        self.brands_found.add(brand)
                        continue

                logger.info(
                    f"\n{'='*55}\n"
                    f"[{combo_idx}/{total_combos}] {brand} — {appliance_type}\n"
                    f"{'='*55}"
                )
                self._scrape_brand_type(brand, appliance_type, max_pages, start_page=1)

        # Final dedup
        self.documents = deduplicate_by_key(self.documents, "ps_number")
        self.total_scraped = len(self.documents)
        logger.info(f"\nDone. Total unique parts: {self.total_scraped}")
        return self._make_result()

    def save_to_json(self, filename: str = "parts_raw.json"):
        """Save scraped data to data/raw/<filename>."""
        save_json(self._make_result(), filename)
        print(f"\nSaved {self.total_scraped} parts -> data/raw/{filename}")

    # ─── Internal ────────────────────────────────────────────────────

    def _make_result(self) -> dict:
        return {
            "metadata": {
                "scraper_type": "parts",
                "scraper_version": "2.0",
                "scraped_date": get_timestamp(),
                "total_documents": len(self.documents),
                "brands_scraped": list(self.brands_found),
                "appliance_types": APPLIANCE_TYPES,
                "failed_urls": len(self.failed_urls),
            },
            "documents": self.documents,
            "failed_urls": self.failed_urls,
        }

    def _scrape_brand_type(self, brand: str, appliance_type: str, max_pages: int, start_page: int = 1):
        """Paginate through all facet search results for one brand × appliance type."""
        cap = max_pages if max_pages > 0 else (MAX_PAGES_PER_CATEGORY or 99_999)
        page = start_page
        consecutive_empty = 0

        # Track seen parts within this specific category to detect pagination rollover / loops
        combo_seen_ps = {
            d["ps_number"]
            for d in self.documents
            if d.get("brand") == brand and d.get("appliance_type") == appliance_type.lower() and d.get("ps_number")
        }

        while page <= cap:
            url = (
                FACET_SEARCH_URL.format(brand=brand, modeltype=appliance_type)
                if page == 1
                else FACET_SEARCH_PAGED_URL.format(brand=brand, modeltype=appliance_type, pagenum=page)
            )

            html = fetch_with_retry(self.session, url)
            if not html:
                self.failed_urls.append(url)
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    logger.warning(f"  3 consecutive fetch failures — stopping {brand} {appliance_type}")
                    break
                page += 1
                continue

            parts = self._parse_listing_page(html, brand, appliance_type)
            if not parts:
                logger.info(f"  Page {page}: no parts found — end of results")
                break

            consecutive_empty = 0
            new = 0
            combo_new = 0
            for p in parts:
                ps = p.get("ps_number")
                if ps:
                    if ps not in combo_seen_ps:
                        combo_seen_ps.add(ps)
                        combo_new += 1
                    if ps not in self._seen_ps:
                        self._seen_ps.add(ps)
                        self.documents.append(p)
                        new += 1

            self.total_scraped = len(self.documents)
            logger.info(f"  Page {page}: {len(parts)} found, {new} new. Total: {self.total_scraped}")

            # Checkpoint every CHECKPOINT_INTERVAL parts
            if new > 0 and self.total_scraped % CHECKPOINT_INTERVAL < new:
                save_checkpoint(
                    self.documents, "parts_checkpoint.json",
                    {"brand": brand, "appliance_type": appliance_type, "last_page": page},
                )

            # If no new parts are discovered within this category on this page, it means pagination wrapped/rolled over
            if len(parts) > 0 and combo_new == 0:
                logger.info(f"  Page {page}: all {len(parts)} parts already seen in this category — end of results")
                break

            # If fewer than 12 results returned, we're on the last page
            if len(parts) < 12:
                logger.info(f"  Last page reached ({len(parts)} < 12)")
                break

            page += 1

        self.brands_found.add(brand)

    def _parse_listing_page(
        self, html: str, brand: str, appliance_type: str
    ) -> list[dict]:
        """
        Parse a facet search listing page into a list of part dicts.

        Each card (div.smart-search__parts__part) contains:
          - a.bold.text-md.text-black.mb-1 → part name link (href has PS#)
          - img → part image
          - Text tokens separated by '|': name | stars | review-count | mfr-label |
                                         MPN | brand-label | brand | $ | price | stock
        """
        soup = BeautifulSoup(html, "html.parser")
        parts = []

        cards = soup.select(LISTING_SELECTORS["card"])
        for i, card in enumerate(cards):
            # ── Name & PS number ──────────────────────────────────────
            name_link = card.select_one(LISTING_SELECTORS["name_link"])
            if not name_link:
                continue
            name = name_link.get_text(strip=True)
            href = name_link.get("href", "")
            ps_number = extract_ps_number(href)
            if not ps_number or not name:
                continue

            detail_url = make_absolute_url(href.split("?")[0])  # Strip tracking params

            # ── Image ─────────────────────────────────────────────────
            img = card.select_one(LISTING_SELECTORS["image"])
            image_url = img.get("src", "") if img else None

            # ── Parse text tokens from the card ──────────────────────
            # The card text (pipe-separated) looks like:
            # "Part Name | ★★★★☆ | (111) | Manufacturer part #: | EDR1RXD1 |
            #  Genuine Part for | Whirlpool | $ | 84.45 | In Stock | Add to cart"
            card_text = card.get_text(separator="|", strip=True)
            tokens = [t.strip() for t in card_text.split("|")]

            # Extract MPN (token after "Manufacturer part #:")
            mpn = ""
            for j, tok in enumerate(tokens):
                if "manufacturer part" in tok.lower():
                    if j + 1 < len(tokens):
                        mpn = tokens[j + 1].strip()
                    break

            # Extract price (token after "$")
            price_cents = 0
            for j, tok in enumerate(tokens):
                if tok.strip() == "$":
                    if j + 1 < len(tokens):
                        price_cents = extract_price_cents(tokens[j + 1])
                    break
            # Fallback: scan tokens for "X.XX" pattern
            if price_cents == 0:
                for tok in tokens:
                    if re.match(r"^\d+\.\d{2}$", tok):
                        price_cents = extract_price_cents(tok)
                        break

            # Extract stock status
            in_stock = True
            card_lower = card_text.lower()
            if "out of stock" in card_lower or "not available" in card_lower or "discontinued" in card_lower:
                in_stock = False

            # Review count (token like "(111)")
            review_count = 0
            for tok in tokens:
                m = re.match(r"^\((\d+)\)$", tok.strip())
                if m:
                    review_count = int(m.group(1))
                    break

            doc = {
                "id":             generate_document_id("part", len(self.documents) + i + 1),
                "source_type":    "part",
                "ps_number":      ps_number,
                "mpn":            mpn,
                "name":           name,
                "description":    "",   # Populated by detail page scrape (optional)
                "price_cents":    price_cents,
                "in_stock":       in_stock,
                "image_url":      image_url,
                "rating":         None,
                "review_count":   review_count,
                "appliance_type": appliance_type.lower(),
                "brand":          brand,
                "video_url":      None,
                "symptoms":       [],
                "alt_numbers":    [mpn] if mpn else [],
                "install_steps":  [],
                "detail_url":     detail_url,
            }
            parts.append(doc)

        return parts


# ─── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape PartSelect parts catalog")
    parser.add_argument("--brands", nargs="+", help="Specific brands to scrape")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages per combo (0=all)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--output", default="parts_raw.json", help="Output filename")
    args = parser.parse_args()

    scraper = PartsScraper()
    brand_count = len(args.brands) if args.brands else len(BRANDS)

    print(f"\n{'='*55}")
    print(f"  PartSelect Parts Scraper v2.0")
    print(f"  Brands: {brand_count}")
    print(f"  Appliance types: {', '.join(APPLIANCE_TYPES)}")
    print(f"  Max pages/combo: {'unlimited' if args.max_pages == 0 else args.max_pages}")
    print(f"  Resume: {args.resume}")
    print(f"{'='*55}\n")

    scraper.scrape_all_appliances(
        brands=args.brands,
        max_pages=args.max_pages,
        resume=args.resume,
    )
    scraper.save_to_json(args.output)

    print(f"\n{'='*55}")
    print(f"  Complete! {scraper.total_scraped} parts scraped")
    print(f"  Brands found: {len(scraper.brands_found)}")
    print(f"  Failed URLs: {len(scraper.failed_urls)}")
    print(f"  Output: data/raw/{args.output}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
