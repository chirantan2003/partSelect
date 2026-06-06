# backend/scraper/blog_scraper.py
"""
Blog article scraper for PartSelect.com.

Scrapes repair articles, how-to guides, error codes, and maintenance
guides from the PartSelect blog across 5 topics.

Features:
  - Pagination support (follows "OLDER" links)
  - Intelligent filtering (only refrigerator/dishwasher content)
  - Interactive prompts every 50 articles
  - Automatic checkpointing every 25 articles
  - Retry logic with exponential backoff

Usage:
    python -m backend.scraper.blog_scraper
    python -m backend.scraper.blog_scraper --topics repair error-codes

Output: data/raw/blogs_raw.json
Runtime: ~5-10 minutes for 50+ articles
"""

import re
import argparse
from bs4 import BeautifulSoup

from backend.scraper.config import (
    BASE_URL, BLOG_URL_TEMPLATE, BLOG_TOPICS,
    BLOG_SELECTORS, CHECKPOINT_INTERVAL, MIN_CONTENT_LENGTH, MAX_CONTENT_LENGTH,
)
from backend.scraper.utils import (
    create_session, fetch_with_retry, make_absolute_url, is_valid_image_url,
    extract_all_ps_numbers, strip_html, extract_first_paragraph,
    truncate_text, validate_content_length,
    extract_multiple_brands, classify_appliance_type,
    extract_youtube_id, youtube_url,
    save_json, save_checkpoint, load_checkpoint,
    deduplicate_by_url, generate_document_id, get_timestamp,
    setup_logging,
)

logger = setup_logging("blog_scraper")

INTERACTIVE_PROMPT_INTERVAL = 50   # Ask user every N articles whether to continue
CHECKPOINT_BLOG_INTERVAL = 25     # Save checkpoint every N articles


class BlogScraper:
    """Scrapes PartSelect blog articles about appliance repair."""

    def __init__(self):
        self.session = create_session()
        self.total_scraped = 0
        self.failed_urls: list[str] = []
        self.documents: list[dict] = []
        self._seen_urls: set[str] = set()

    # ─── Public API ──────────────────────────────────────────────────

    def scrape_all_topics(
        self,
        topics: list[str] | None = None,
        resume: bool = False,
        interactive: bool = False,
    ) -> dict:
        """Scrape all blog topics."""
        if resume:
            existing = load_checkpoint("blogs_checkpoint.json")
            if existing:
                self.documents = existing
                self._seen_urls = {d["source_url"] for d in existing}
                logger.info(f"Resumed: {len(existing)} articles")

        for topic in (topics or BLOG_TOPICS):
            logger.info(f"\n{'='*50}\nTopic: {topic}\n{'='*50}")
            self._scrape_topic(topic, interactive=interactive)

        self.documents = deduplicate_by_url(self.documents)
        self.total_scraped = len(self.documents)
        return self._make_result()

    def save_to_json(self, filename: str = "blogs_raw.json"):
        """Save scraped data to data/raw/<filename>."""
        save_json(self._make_result(), filename)
        print(f"\nSaved {self.total_scraped} articles -> data/raw/{filename}")

    # ─── Internal ────────────────────────────────────────────────────

    def _make_result(self) -> dict:
        return {
            "metadata": {
                "scraper_type": "blog",
                "scraper_version": "1.0",
                "scraped_date": get_timestamp(),
                "total_documents": self.total_scraped,
                "failed_urls": len(self.failed_urls),
            },
            "documents": self.documents,
            "failed_urls": self.failed_urls,
        }

    def _scrape_topic(self, topic: str, interactive: bool = False):
        """Scrape all pages for one topic, following pagination."""
        url: str | None = BLOG_URL_TEMPLATE.format(topic=topic)
        page_num = 0
        MAX_PAGES = 100  # Safety cap

        while url and page_num < MAX_PAGES:
            page_num += 1
            logger.info(f"  Page {page_num}: {url}")

            html = fetch_with_retry(self.session, url)
            if not html:
                self.failed_urls.append(url)
                break

            soup = BeautifulSoup(html, "html.parser")
            article_links = self._parse_article_links(soup, topic)
            logger.info(f"  Found {len(article_links)} article links")

            for article_url in article_links:
                if article_url in self._seen_urls:
                    continue
                self._seen_urls.add(article_url)

                doc = self._scrape_article(article_url, topic)
                if doc:
                    self.documents.append(doc)
                    self.total_scraped = len(self.documents)

                    # Checkpoint
                    if self.total_scraped % CHECKPOINT_BLOG_INTERVAL == 0:
                        save_checkpoint(
                            self.documents, "blogs_checkpoint.json",
                            {"topic": topic, "page": page_num}
                        )
                        logger.info(f"  Checkpoint: {self.total_scraped} articles saved")

                    # Interactive prompt
                    if interactive and self.total_scraped % INTERACTIVE_PROMPT_INTERVAL == 0:
                        answer = input(f"\n  [{self.total_scraped} articles scraped] Continue? [Y/n]: ").strip().lower()
                        if answer == "n":
                            logger.info("  User stopped scraping.")
                            return

            url = self._find_older_link(soup)

    def _parse_article_links(self, soup: BeautifulSoup, topic: str) -> list[str]:
        """Extract article page URLs from a topic listing page."""
        topic_path = f"/blog/topics/{topic}/"
        links = []
        for a in soup.select("a[href*='/blog/']"):
            href = a.get("href", "")
            if href and "/blog/" in href and href != topic_path and href != "/blog/":
                full = make_absolute_url(href)
                if full not in links:
                    links.append(full)
        return links

    def _scrape_article(self, url: str, topic: str) -> dict | None:
        """Scrape one blog article and return a structured document."""
        html = fetch_with_retry(self.session, url)
        if not html:
            self.failed_urls.append(url)
            return None

        soup = BeautifulSoup(html, "html.parser")

        # ── Title ─────────────────────────────────────────────────────
        h1 = soup.select_one("h1")
        title = h1.get_text(strip=True) if h1 else ""
        if not title:
            return None

        # ── Subtitle ──────────────────────────────────────────────────
        h2 = soup.select_one("h2")
        subtitle = h2.get_text(strip=True) if h2 else ""

        # ── Content ───────────────────────────────────────────────────
        content_html = ""
        for sel in BLOG_SELECTORS["content"].split(", "):
            el = soup.select_one(sel.strip())
            if el:
                content_html = str(el)
                break

        content_text = strip_html(content_html)
        if not validate_content_length(content_text, MIN_CONTENT_LENGTH):
            return None  # Too short / too long
        content_text = truncate_text(content_text, MAX_CONTENT_LENGTH)

        # ── Appliance type filter ─────────────────────────────────────
        appliance_type = classify_appliance_type(f"{title} {content_text[:500]}")
        if not appliance_type:
            return None  # Skip non-appliance content

        # ── Images ────────────────────────────────────────────────────
        images = []
        for img in soup.select(BLOG_SELECTORS["image"]):
            src = img.get("src", "")
            if src and is_valid_image_url(src) and src not in images:
                images.append(make_absolute_url(src))
                if len(images) >= 10:
                    break

        # ── Videos ────────────────────────────────────────────────────
        videos = []
        for el in soup.select("[data-video-id], iframe[src*='youtube'], a[href*='youtu']"):
            vid = extract_youtube_id(el)
            if vid:
                video_url_str = youtube_url(vid)
                if video_url_str not in videos:
                    videos.append(video_url_str)

        # ── Brands ────────────────────────────────────────────────────
        brands = extract_multiple_brands(f"{title} {content_text[:800]}")

        # ── Part numbers ───────────────────────────────────────────────
        ps_numbers = extract_all_ps_numbers(content_html)

        doc = {
            "id":             generate_document_id("blog", self.total_scraped + 1),
            "source_type":    "blog_article",
            "topic":          topic,
            "title":          title,
            "subtitle":       subtitle,
            "content_html":   content_html[:50_000],
            "content_text":   content_text,
            "images":         images,
            "videos":         videos,
            "brands":         brands,
            "appliance_type": appliance_type,
            "ps_numbers":     ps_numbers,
            "source_url":     url,
        }

        logger.info(f"    ✓ {title[:55]} [{appliance_type}]")
        return doc

    def _find_older_link(self, soup: BeautifulSoup) -> str | None:
        """Find the 'OLDER' pagination link to navigate to the next page."""
        # Try rel="next"
        rel_next = soup.select_one("a[rel='next']")
        if rel_next and rel_next.get("href"):
            return make_absolute_url(rel_next["href"])

        # Try text matching
        for a in soup.find_all("a"):
            text = a.get_text(strip=True).upper()
            if text in ("OLDER", "OLDER POSTS", "NEXT", "NEXT PAGE", "LOAD MORE", "→"):
                href = a.get("href", "")
                if href:
                    return make_absolute_url(href)

        return None


# ─── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape PartSelect blog articles")
    parser.add_argument("--topics", nargs="+", help="Specific topics (default: all 5)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--interactive", action="store_true", help="Prompt every 50 articles")
    parser.add_argument("--output", default="blogs_raw.json")
    args = parser.parse_args()

    scraper = BlogScraper()
    topics = args.topics or BLOG_TOPICS

    print(f"\n{'='*50}")
    print(f"  PartSelect Blog Scraper v1.0")
    print(f"  Topics: {', '.join(topics)}")
    print(f"  Resume: {args.resume}")
    print(f"  Interactive: {args.interactive}")
    print(f"{'='*50}\n")

    scraper.scrape_all_topics(topics=args.topics, resume=args.resume, interactive=args.interactive)
    scraper.save_to_json(args.output)

    print(f"\n{'='*50}")
    print(f"  Complete! {scraper.total_scraped} articles scraped")
    print(f"  Failed: {len(scraper.failed_urls)}")
    print(f"  Output: data/raw/{args.output}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
