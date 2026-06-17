"""
PDF extraction pipeline — Tier 1 and Tier 2 event types only.

Tier 1 (table extraction + text): results, board_meeting, merger_acquisition
Tier 2 (text + keyword): management_change, rating_change, litigation, agreement

Skips: update, administrative, investor_presentation, disclosure, other, shareholder_meeting
"""
import logging
import time
import fitz
import json
import re
from pathlib import Path
from io import BytesIO

import httpx
from sqlalchemy import select, text
from app.db import SessionLocal
from app.models.event import Event
from app.models.raw_document import RawDocument
from app.models.pdf_extracts import PdfExtract

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TIER1 = {"results", "board_meeting", "merger_acquisition"}
TIER2 = {"management_change", "rating_change", "litigation", "agreement", "news_verification"}
TARGET_TYPES = TIER1 | TIER2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}
REQUEST_DELAY = 0.5  # seconds between requests — be a good citizen

# ── Label constants for KV extraction ─────────────────────────────────────────

REVENUE_LABELS = {
    "revenue from operations",
    "net sales",
    "total income",
    "net interest income",           # banks (ICICIBANK, HDFCBANK, etc.)
    "income from operations",
    "total revenue",
}

PAT_LABELS = {
    "profit after tax",
    "profit/(loss) after tax",
    "profit / (loss) after tax",
    "net profit",
    "net profit/(loss)",
    "profit for the period",
    "profit for the year",
    "profit/(loss) for the period",
    "profit/(loss) for the year",
}

EPS_LABELS = {
    "earnings per share",
    "earning per share",
    "basic and diluted earnings per share",
    "basic and diluted earning per share",
}

def _label_matches(label: str, candidates: set) -> bool:
    """True if any candidate string appears as a substring of label."""
    return any(c in label for c in candidates)


def _detect_period_columns(table: list) -> tuple:
    """
    Scan first 4 rows for date/period strings to identify which column holds
    the current period and which holds the same period prior year.

    Logic:
    - Parse year and month from each cell (FY notation preferred over calendar year).
    - Current column = most recent (year, month).
    - Prior-year column = column with same month but year - 1.
    - Fallback → (1, 3), which is correct for the standard NSE 5-column format:
        Col 0: Particulars | Col 1: Q_current | Col 2: Q_prev_qtr | Col 3: Q_prev_yr | ...
    """
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    col_dates = {}  # col_idx → (year, month)

    for row in table[:4]:
        for col_idx, cell in enumerate(row):
            if not cell or col_idx == 0:  # col 0 is always the label column
                continue
            cell_str = str(cell).replace("\n", " ")

            # FY notation takes precedence: FY25, FY2025
            year = None
            fy = re.search(r'\bFY\s*(\d{2,4})\b', cell_str, re.IGNORECASE)
            if fy:
                y = int(fy.group(1))
                year = y + 2000 if y < 100 else y
            else:
                cal = re.search(r'\b(20\d{2})\b', cell_str)
                if cal:
                    year = int(cal.group(1))

            month = 0
            for mname, mnum in month_map.items():
                if mname in cell_str.lower():
                    month = mnum
                    break

            if year and col_idx not in col_dates:
                col_dates[col_idx] = (year, month)

    if len(col_dates) < 2:
        return 1, 3  # fallback: standard NSE 5-column format

    sorted_cols = sorted(col_dates.items(), key=lambda x: x[1], reverse=True)
    current_col = sorted_cols[0][0]
    current_year, current_month = col_dates[current_col]

    # Find the column with same month but year - 1 (same quarter, prior year)
    prior_yr_col = None
    for col_idx, (year, month) in sorted_cols:
        if year == current_year - 1 and (month == 0 or month == current_month):
            prior_yr_col = col_idx
            break

    if prior_yr_col is None:
        # Fallback: take the column with the oldest date
        prior_yr_col = sorted_cols[-1][0]

    return current_col, prior_yr_col


# ── KV extractor ───────────────────────────────────────────────────────────────


# ── PDF fetch and extraction ───────────────────────────────────────────────────

def fetch_pdf(url: str, retries: int = 2) -> bytes | None:
    for attempt in range(retries + 1):
        try:
            r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=60)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                return r.content
            logger.warning("Status %s for %s", r.status_code, url)
            return None
        except Exception as e:
            logger.warning("Attempt %d failed for %s: %s", attempt + 1, url, e)
            if attempt < retries:
                time.sleep(5 * (attempt + 1))
    return None


def extract_pdf(pdf_bytes: bytes) -> dict:
    result = {
        "full_text": "",
        "press_release_text": "",
        "tables_json": [],
        "page_count": 0,
    }

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    result["page_count"] = len(doc)

    all_text_parts = []
    all_tables = []

    for page in doc:
        page_text = page.get_text()
        if page_text:
            all_text_parts.append(page_text)

        tabs = page.find_tables()
        for tab in tabs:
            extracted = tab.extract()
            cleaned = [
                [cell.strip() if cell else "" for cell in row]
                for row in extracted
                if any(cell for cell in row)
            ]
            if len(cleaned) > 1:
                all_tables.append(cleaned)

    result["full_text"] = "\n".join(all_text_parts)
    result["tables_json"] = all_tables

    pr_match = re.search(
        r'(Annexure\s+["\']?B["\']?|Media Release|Press Release)',
        result["full_text"], re.IGNORECASE
    )
    result["press_release_text"] = (
        result["full_text"][pr_match.start():] if pr_match else result["full_text"]
    )

    return result


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run(limit: int = None, event_type: str = None):
    db = SessionLocal()

    filter_clause = "AND e.event_type = :etype" if event_type else ""
    query = text(f"""
        SELECT e.id, e.event_type, rd.source_url, rd.id as rd_id
        FROM events e
        JOIN raw_documents rd ON rd.id = e.document_id
        LEFT JOIN pdf_extracts pe ON pe.event_id = e.id
        WHERE e.event_type = ANY(:types)
        AND rd.source_url LIKE '%.pdf'
        AND pe.id IS NULL
        {filter_clause}
        ORDER BY e.event_timestamp ASC
    """)

    params = {"types": list(TARGET_TYPES)}
    if event_type:
        params["etype"] = event_type

    rows = db.execute(query, params).fetchall()

    if limit:
        rows = rows[:limit]

    logger.info("Found %d events to process", len(rows))

    success, failed = 0, 0

    for event_id, event_type_val, url, rd_id in rows:
        try:
            pdf_bytes = fetch_pdf(url)

            if not pdf_bytes:
                db.add(PdfExtract(
                    event_id=event_id, raw_document_id=rd_id,
                    extraction_status="failed",
                    extraction_error="fetch_failed"
                ))
                db.commit()
                failed += 1
                continue

            extracted = extract_pdf(pdf_bytes)

            # KV extraction only makes sense for results PDFs which have
            # a structured P&L table. board_meeting and merger_acquisition
            # documents don't follow the quarterly results table format.
            kv = {}

            db.add(PdfExtract(
                event_id=event_id,
                raw_document_id=rd_id,
                full_text=extracted["full_text"],
                press_release_text=extracted["press_release_text"],
                tables_json=extracted["tables_json"],
                kv_pairs=kv,
                page_count=extracted["page_count"],
                extraction_status="success"
            ))
            db.commit()
            success += 1

            if success % 50 == 0:
                logger.info("Progress: %d success / %d failed", success, failed)

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            logger.error("Event %d failed: %s", event_id, e)
            db.rollback()
            db.add(PdfExtract(
                event_id=event_id, raw_document_id=rd_id,
                extraction_status="failed",
                extraction_error=str(e)[:500]
            ))
            db.commit()
            failed += 1

    logger.info("Done — success: %d, failed: %d", success, failed)
    db.close()


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    etype = sys.argv[2] if len(sys.argv) > 2 else None
    run(limit=limit, event_type=etype)