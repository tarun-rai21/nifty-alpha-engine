"""
Targeted retry for tickers that timed out or returned zero records.
Uses longer timeouts, larger chunks, and session refresh mid-run.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from jugaad_data.nse import NSELive
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.raw_document import RawDocument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NSE_URL = "https://www.nseindia.com/api/corporate-announcements"
FROM_DATE = datetime(2022, 1, 1, tzinfo=timezone.utc)

# Tickers to retry — extend this list as needed
RETRY_TICKERS = [
    "ADANIENT", "ADANIPORTS", "BEL", "BHARTIARTL",
    "GRASIM", "SUNPHARMA", "ULTRACEMCO",
]


def parse_sort_date(raw: str) -> Optional[datetime]:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def fetch_with_retry(nsl: NSELive, symbol: str, from_dt: datetime,
                     to_dt: datetime, retries: int = 3) -> list[dict]:
    for attempt in range(1, retries + 1):
        try:
            resp = nsl.s.get(
                NSE_URL,
                params={
                    "index": "equities",
                    "symbol": symbol,
                    "from_date": from_dt.strftime("%d-%m-%Y"),
                    "to_date": to_dt.strftime("%d-%m-%Y"),
                },
                timeout=30,  # doubled from original 15s
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            wait = attempt * 5  # 5s, 10s, 15s
            logger.warning("[%s] attempt %d/%d failed: %s — retrying in %ds",
                           symbol, attempt, retries, exc, wait)
            if attempt < retries:
                time.sleep(wait)
    return []


def ingest_ticker(db: Session, nsl: NSELive, ticker: str,
                  chunk_days: int = 180) -> tuple[int, int]:
    """Use 180-day chunks — fewer API calls, less chance of mid-ticker rate limiting."""
    inserted = skipped = 0
    end = datetime.now(tz=timezone.utc)
    chunk_start = FROM_DATE

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end)
        records = fetch_with_retry(nsl, ticker, chunk_start, chunk_end)

        for ann in records:
            seq_id = str(ann.get("seq_id", "")).strip()
            if not seq_id:
                continue

            checksum = f"nse_{seq_id}"
            exists = db.execute(
                select(RawDocument.id).where(RawDocument.checksum == checksum)
            ).first()
            if exists:
                skipped += 1
                continue

            db.add(RawDocument(
                source_name="NSE Corporate Filings",
                source_type="nse_announcement",
                source_url=ann.get("attchmntFile") or NSE_URL,
                published_at=parse_sort_date(ann.get("sort_date", "")),
                raw_text=ann.get("attchmntText", "").strip(),
                raw_html=json.dumps(ann),
                checksum=checksum,
                ticker=ticker,
            ))
            inserted += 1

        db.commit()
        chunk_start = chunk_end
        time.sleep(2.0)  # longer inter-chunk delay

    return inserted, skipped


def run() -> None:
    db = SessionLocal()
    try:
        total_ins = total_skip = 0
        for i, ticker in enumerate(RETRY_TICKERS, 1):
            logger.info("[%d/%d] Retrying %s", i, len(RETRY_TICKERS), ticker)

            # Fresh NSE session every 3 tickers — prevents cookie expiry mid-run
            if i % 3 == 1:
                nsl = NSELive()
                logger.info("NSE session refreshed")

            ins, skip = ingest_ticker(db, nsl, ticker)
            logger.info("  inserted=%d  skipped=%d", ins, skip)
            total_ins += ins
            total_skip += skip
            time.sleep(4.0)  # longer inter-ticker delay for rate limit recovery

        logger.info("Done. inserted=%d  skipped=%d", total_ins, total_skip)
    finally:
        db.close()


if __name__ == "__main__":
    run()