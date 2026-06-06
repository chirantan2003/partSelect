# backend/scraper/repair_scraper.py
"""
Repair symptom scraper for PartSelect.com.

Scrapes all refrigerator and dishwasher symptom guides:
  - Symptom name and difficulty level
  - Repair story / introduction text
  - YouTube video tutorials with thumbnails
  - Related parts with descriptions
  - Step-by-step inspection instructions

Two-level scraping:
  1. Index page (/Repair/{Appliance}/) → discover all symptom URLs
  2. Individual symptom pages → extract full content

Usage:
    python -m backend.scraper.repair_scraper

Output: data/raw/repair_symptoms_raw.json
Runtime: ~90 seconds for all 21 symptoms
"""

import re
import argparse
from bs4 import BeautifulSoup

from backend.scraper.config import (
    BASE_URL, REPAIR_INDEX_URL, REPAIR_SYMPTOM_URL,
    REFRIGERATOR_SYMPTOMS, DISHWASHER_SYMPTOMS, APPLIANCE_TYPES,
)
from backend.scraper.utils import (
    create_session, fetch_with_retry, make_absolute_url,
    extract_ps_number, extract_all_ps_numbers,
    extract_youtube_id, youtube_url, youtube_thumbnail,
    save_json, generate_document_id, get_timestamp,
    setup_logging,
)

logger = setup_logging("repair_scraper")


class RepairScraper:
    """Scrapes PartSelect repair symptom guides."""

    def __init__(self):
        self.session = create_session()
        self.total_scraped = 0
        self.failed_urls: list[str] = []
        self.documents: list[dict] = []

    # ─── Public API ──────────────────────────────────────────────────

    def scrape_all_appliances(self) -> dict:
        """
        Scrape all repair symptoms for refrigerators and dishwashers.
        Uses hardcoded symptom slugs discovered from the live site.
        """
        symptom_map = {
            "Refrigerator": REFRIGERATOR_SYMPTOMS,
            "Dishwasher":   DISHWASHER_SYMPTOMS,
        }
        doc_index = 0

        for appliance_type, symptoms in symptom_map.items():
            logger.info(f"\n{'='*50}")
            logger.info(f"Scraping {appliance_type} symptoms ({len(symptoms)})")
            logger.info(f"{'='*50}")

            for i, (slug, display_name) in enumerate(symptoms, 1):
                url = REPAIR_SYMPTOM_URL.format(
                    appliance_type=appliance_type, slug=slug
                )
                logger.info(f"  [{i}/{len(symptoms)}] {display_name}: {url}")

                doc = self._scrape_symptom_page(
                    url, appliance_type.lower(), slug, display_name, doc_index
                )
                if doc:
                    self.documents.append(doc)
                    self.total_scraped += 1
                    doc_index += 1
                else:
                    self.failed_urls.append(url)

        return self._make_result()

    def save_to_json(self, filename: str = "repair_symptoms_raw.json"):
        """Save scraped data to data/raw/<filename>."""
        save_json(self._make_result(), filename)
        print(f"\nSaved {self.total_scraped} symptoms -> data/raw/{filename}")

    # ─── Internal ────────────────────────────────────────────────────

    def _make_result(self) -> dict:
        return {
            "metadata": {
                "scraper_type": "repair",
                "scraper_version": "1.0",
                "scraped_date": get_timestamp(),
                "total_documents": self.total_scraped,
                "failed_urls": len(self.failed_urls),
            },
            "documents": self.documents,
            "failed_urls": self.failed_urls,
        }

    def _scrape_symptom_page(
        self, url: str, appliance_type: str, slug: str, display_name: str, index: int
    ) -> dict | None:
        """Scrape one symptom page and extract all relevant data."""
        html = fetch_with_retry(self.session, url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # ── Title ────────────────────────────────────────────────────
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else display_name

        # ── Difficulty level ─────────────────────────────────────────
        difficulty = "Unknown"
        diff_patterns = re.compile(r"\b(EASY|MEDIUM|HARD|SIMPLE|COMPLEX)\b", re.I)
        # Search in various places
        for el in soup.select("[class*='difficulty'], [class*='level'], strong, b, .badge"):
            text = el.get_text(strip=True).upper()
            m = diff_patterns.search(text)
            if m:
                difficulty = m.group(1)
                break
        # Fallback: scan full page text
        if difficulty == "Unknown":
            m = diff_patterns.search(soup.get_text())
            if m:
                difficulty = m.group(1)

        # ── Repair story / intro text ─────────────────────────────────
        repair_story = ""
        for sel in [
            "[class*='repair-story']", "[class*='symptom-desc']", "[class*='intro']",
            ".repair-content", "main article p", "main section p",
        ]:
            els = soup.select(sel)
            if els:
                repair_story = " ".join(e.get_text(strip=True) for e in els[:3])
                if len(repair_story) > 100:
                    break
        # Final fallback: first meaningful paragraphs
        if len(repair_story) < 100:
            paras = [
                p.get_text(strip=True)
                for p in soup.select("main p, article p")
                if len(p.get_text(strip=True)) > 50
            ]
            # Skip cookie/geo pop-up text
            paras = [p for p in paras if "cookie" not in p.lower() and "canadian" not in p.lower()]
            repair_story = " ".join(paras[:4])

        # ── YouTube video ─────────────────────────────────────────────
        video_info = None
        for el in soup.select("[data-video-id], iframe[src*='youtube'], a[href*='youtu']"):
            vid = extract_youtube_id(el)
            if vid:
                video_info = {
                    "video_id":  vid,
                    "url":       youtube_url(vid),
                    "thumbnail": youtube_thumbnail(vid),
                }
                break

        # ── Related parts ─────────────────────────────────────────────
        related_parts = []
        seen_ps = set()

        # Parts are usually in section containers; grab links near part names
        part_sections = soup.select(
            "[class*='parts-for'], [class*='symptom-parts'], [class*='related-parts'],"
            "[class*='rd-parts'], [class*='part-card']"
        )
        if part_sections:
            for section in part_sections:
                for link in section.select("a[href*='/PS']"):
                    self._add_part_from_link(link, seen_ps, related_parts)
        else:
            # Fallback: all PS links on page
            for link in soup.select("a[href*='/PS']"):
                self._add_part_from_link(link, seen_ps, related_parts)

        # ── Inspection steps ──────────────────────────────────────────
        inspection_steps = []
        step_selectors = [
            ".inspection-steps li", ".repair-steps li",
            "[class*='step-list'] li", "main ol li",
        ]
        for sel in step_selectors:
            steps = soup.select(sel)
            if steps:
                inspection_steps = [
                    {"step_no": i + 1, "text": s.get_text(strip=True)}
                    for i, s in enumerate(steps)
                    if len(s.get_text(strip=True)) > 10
                ]
                if inspection_steps:
                    break

        # ── All PS numbers referenced on the page ─────────────────────
        all_ps = extract_all_ps_numbers(html)

        doc = {
            "id":               generate_document_id("repair", index),
            "source_type":      "repair_symptom",
            "appliance_type":   appliance_type,
            "symptom_name":     display_name,
            "symptom_slug":     slug,
            "title":            title,
            "difficulty":       difficulty,
            "repair_story":     repair_story,
            "video":            video_info,
            "related_parts":    related_parts,
            "inspection_steps": inspection_steps,
            "all_ps_numbers":   all_ps,
            "source_url":       url,
        }

        logger.info(
            f"    ✓ {title[:50]} | {difficulty} | "
            f"{len(related_parts)} parts | {len(inspection_steps)} steps"
        )
        return doc

    def _add_part_from_link(self, link, seen_ps: set, target: list):
        """Helper to extract a part dict from a PS link and append to target."""
        href = link.get("href", "")
        ps = extract_ps_number(href)
        if not ps or ps in seen_ps:
            return
        name = link.get_text(strip=True)
        if not name or len(name) < 3:
            return
        seen_ps.add(ps)

        # Look for description nearby
        desc = ""
        parent = link.find_parent("div") or link.find_parent("li")
        if parent:
            for el in parent.select("p, span[class*='desc'], .part-description"):
                text = el.get_text(strip=True)
                if text and text != name:
                    desc = text
                    break

        target.append({
            "ps_number": ps,
            "name":      name,
            "description": desc,
            "url":       make_absolute_url(href.split("?")[0]),
        })


# ─── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape PartSelect repair symptoms")
    parser.add_argument("--output", default="repair_symptoms_raw.json")
    args = parser.parse_args()

    scraper = RepairScraper()
    total_symptoms = len(REFRIGERATOR_SYMPTOMS) + len(DISHWASHER_SYMPTOMS)

    print(f"\n{'='*50}")
    print(f"  PartSelect Repair Scraper v1.0")
    print(f"  Symptoms: {len(REFRIGERATOR_SYMPTOMS)} refrigerator + {len(DISHWASHER_SYMPTOMS)} dishwasher")
    print(f"  Total: {total_symptoms} symptom pages")
    print(f"{'='*50}\n")

    scraper.scrape_all_appliances()
    scraper.save_to_json(args.output)

    print(f"\n{'='*50}")
    print(f"  Complete! {scraper.total_scraped}/{total_symptoms} symptoms scraped")
    print(f"  Failed: {len(scraper.failed_urls)}")
    print(f"  Output: data/raw/{args.output}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
