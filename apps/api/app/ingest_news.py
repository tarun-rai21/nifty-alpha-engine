"""
India financial news ingestion via Google News RSS (per-ticker queries).
Rationale: Indian financial site RSS feeds produce malformed XML consistently.
Google News RSS produces valid XML, is ticker-specific, and requires no API key.
"""
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus

import feedparser
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.company import Company
from app.models.raw_document import RawDocument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GNEWS_BASE = "https://news.google.com/rss/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def gnews_url(company_name: str) -> str:
    query = f"{company_name} NSE India stock"
    return f"{GNEWS_BASE}?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"


def make_checksum(url: str, title: str) -> str:
    raw = f"{url}|{title}".strip().lower()
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def parse_date(entry) -> Optional[datetime]:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    if hasattr(entry, "published") and entry.published:
        try:
            return parsedate_to_datetime(entry.published).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def get_text(entry) -> str:
    parts = []
    if getattr(entry, "title", ""):
        parts.append(entry.title.strip())
    if getattr(entry, "summary", ""):
        clean = re.sub(r"<[^>]+>", " ", entry.summary)
        parts.append(re.sub(r"\s+", " ", clean).strip())
    return "\n".join(parts)


def fetch_gnews(url: str) -> list:
    """Fetch with requests then parse bytes — avoids feedparser's user-agent and encoding issues."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        return parsed.entries or []
    except Exception as exc:
        logger.warning("Fetch failed for %s: %s", url, exc)
        return []


def ingest_ticker(db: Session, ticker: str, company_name: str) -> tuple[int, int]:
    inserted = skipped = 0
    entries = fetch_gnews(gnews_url(company_name))

    if not entries:
        logger.warning("[%s] no entries returned", ticker)
        return 0, 0

    for entry in entries:
        title = getattr(entry, "title", "") or ""
        link = getattr(entry, "link", "") or ""
        if not title:
            continue

        checksum = make_checksum(link, title)
        if db.execute(select(RawDocument.id).where(RawDocument.checksum == checksum)).first():
            skipped += 1
            continue

        db.add(RawDocument(
            source_name="Google News",
            source_type="news",
            source_url=link,
            published_at=parse_date(entry),
            raw_text=get_text(entry),
            raw_html=json.dumps({
                "title": title,
                "link": link,
                "summary": getattr(entry, "summary", ""),
                "published": getattr(entry, "published", ""),
                "ticker": ticker,
                "company": company_name,
            }),
            checksum=checksum,
            ticker=ticker,   # known — no matching needed
        ))
        inserted += 1

    db.commit()
    return inserted, skipped


def run() -> None:
    db = SessionLocal()
    try:
        companies = db.execute(
            select(Company.ticker, Company.company_name).where(Company.is_active == True)
        ).fetchall()
        logger.info("Tickers: %d", len(companies))

        total_ins = total_skip = 0
        for i, (ticker, company_name) in enumerate(companies, 1):
            logger.info("[%d/%d] %s", i, len(companies), ticker)
            ins, skip = ingest_ticker(db, ticker, company_name)
            logger.info("  inserted=%d  skipped=%d", ins, skip)
            total_ins += ins
            total_skip += skip
            time.sleep(2.0)

        logger.info("Done. inserted=%d  skipped=%d", total_ins, total_skip)
    finally:
        db.close()


if __name__ == "__main__":
    run()