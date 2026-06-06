# backend/scraper/__init__.py
from backend.scraper.parts_scraper import PartsScraper
from backend.scraper.repair_scraper import RepairScraper
from backend.scraper.blog_scraper import BlogScraper

__all__ = ["PartsScraper", "RepairScraper", "BlogScraper"]
