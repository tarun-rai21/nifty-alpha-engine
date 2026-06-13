import logging
import os
import time
import warnings
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from jugaad_data.nse import stock_df
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.company import Company
from app.models.price_history import PriceHistory

# jugaad-data emits numpy timezone warnings — harmless, suppress them
warnings.filterwarnings("ignore", category=UserWarning, module="jugaad_data")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FROM_DATE = date(2022, 1, 1)
TO_DATE = date.today()

# Confirmed column names from jugaad-data (June 2026)
# Fallbacks kept in case NSE API changes column names again
_DATE_CANDIDATES   = ["DATE", "CH_TIMESTAMP", "mTIMESTAMP", "TIMESTAMP"]
_COL_MAP = {
    "open":   ["OPEN",   "CH_OPENING_PRICE"],
    "high":   ["HIGH",   "CH_TRADE_HIGH_PRICE"],
    "low":    ["LOW",    "CH_TRADE_LOW_PRICE"],
    "close":  ["CLOSE",  "CH_CLOSING_PRICE"],
    "volume": ["VOLUME", "CH_TOT_TRADED_QTY"],
}


def _ensure_cache_dir() -> None:
    """
    jugaad-data stores cache at ~/.cache/nsehistory-stock.
    If a FILE exists at that path instead of a directory, jugaad-data
    fails with [Errno 17]. Remove the file and create the directory.
    """
    cache_path = Path.home() / ".cache" / "nsehistory-stock"
    if cache_path.exists() and not cache_path.is_dir():
        logger.warning("Cache path is a file, not a directory. Removing: %s", cache_path)
        os.remove(cache_path)
    cache_path.mkdir(parents=True, exist_ok=True)


def _detect_date_col(df: pd.DataFrame) -> Optional[str]:
    for candidate in _DATE_CANDIDATES:
        if candidate in df.columns:
            return candidate
    return None


def _resolve_col(df: pd.DataFrame, field: str) -> Optional[str]:
    for candidate in _COL_MAP[field]:
        if candidate in df.columns:
            return candidate
    logger.warning("Column for '%s' not found. Tried: %s", field, _COL_MAP[field])
    return None


def fetch_and_store(ticker: str, db: Session) -> int:
    try:
        df = stock_df(
            symbol=ticker,
            from_date=FROM_DATE,
            to_date=TO_DATE,
            series="EQ",
        )
    except Exception as e:
        logger.warning("Fetch failed for %s: %s", ticker, e)
        return 0

    if df is None or df.empty:
        logger.warning("No data returned for %s", ticker)
        return 0

    date_col  = _detect_date_col(df)
    if date_col is None:
        logger.error("No date column found for %s. Columns: %s", ticker, list(df.columns))
        return 0

    open_col   = _resolve_col(df, "open")
    high_col   = _resolve_col(df, "high")
    low_col    = _resolve_col(df, "low")
    close_col  = _resolve_col(df, "close")
    volume_col = _resolve_col(df, "volume")

    rows = []
    parse_errors = 0

    for _, row in df.iterrows():
        try:
            close_val = float(row[close_col]) if close_col and pd.notna(row[close_col]) else None
            rows.append({
                "ticker":         ticker,
                "date":           pd.to_datetime(row[date_col]).date(),
                "open":           float(row[open_col])   if open_col   and pd.notna(row[open_col])   else None,
                "high":           float(row[high_col])   if high_col   and pd.notna(row[high_col])   else None,
                "low":            float(row[low_col])    if low_col    and pd.notna(row[low_col])    else None,
                "close":          close_val,
                "volume":         int(row[volume_col])   if volume_col and pd.notna(row[volume_col]) else None,
                # NSE does not provide split-adjusted prices.
                # adjusted_close == close for MVP. Document in methodology.
                "adjusted_close": close_val,
            })
        except Exception as e:
            parse_errors += 1
            if parse_errors <= 3:
                logger.warning("Row parse error for %s: %s", ticker, e)
            continue

    if parse_errors > 3:
        logger.warning("%s: %d total parse errors (first 3 logged)", ticker, parse_errors)

    if not rows:
        return 0

    stmt = insert(PriceHistory).values(rows)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_price_ticker_date")
    db.execute(stmt)
    db.commit()
    return len(rows)


def ingest_all_prices(db: Session) -> None:
    _ensure_cache_dir()

    tickers = db.scalars(
        select(Company.ticker).where(Company.is_active == True)
    ).all()

    logger.info("Fetching prices for %d tickers from NSE...", len(tickers))

    total = 0
    failed = []

    for i, ticker in enumerate(tickers, 1):
        logger.info("[%d/%d] %s", i, len(tickers), ticker)
        inserted = fetch_and_store(ticker, db)

        if inserted == 0:
            failed.append(ticker)
        else:
            logger.info("  → %d rows inserted", inserted)
            total += inserted

        time.sleep(1.5)

    logger.info("Done. %d total rows inserted.", total)
    if failed:
        logger.warning("No data retrieved for: %s", failed)


if __name__ == "__main__":
    with SessionLocal() as db:
        ingest_all_prices(db)