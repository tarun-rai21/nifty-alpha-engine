"""
Phase 3 — Market context feature generation.
For each non-administrative event, computes pre-event returns,
volume z-score, and realized volatility from price_history.

Look-ahead bias rule: all features computed on rows strictly before
event_timestamp.date(). Reference price = T-1 trading day close.

Uses adjusted_close for return calculations to handle corporate actions
(dividends, splits) correctly. Raw close would give spurious returns on
ex-dividend dates.
"""
import logging
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.company import Company
from app.models.event import Event
from app.models.feature import Feature
from app.models.price_history import PriceHistory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FEATURE_VERSION = "v1"
EXCLUDE_TYPES = {"administrative"}
MIN_HISTORY_ROWS = 22   # need 20 days of returns + 2 buffer


# ── Price loading ──────────────────────────────────────────────────────────────

def load_prices(db: Session, ticker: str) -> pd.DataFrame:
    """
    Load full price history for one ticker, sorted ascending by date.
    Returns DataFrame with columns: date, close, adjusted_close, volume, daily_return.
    """
    rows = db.execute(
        select(
            PriceHistory.date,
            PriceHistory.close,
            PriceHistory.adjusted_close,
            PriceHistory.volume,
        )
        .where(PriceHistory.ticker == ticker)
        .order_by(PriceHistory.date)
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "close", "adjusted_close", "volume"])
    df["date"] = pd.to_datetime(df["date"])

    # Daily return on adjusted_close — handles splits/dividends correctly
    df["daily_return"] = df["adjusted_close"].pct_change()

    return df.reset_index(drop=True)


# ── Feature computation ────────────────────────────────────────────────────────

def compute_market_features(prices: pd.DataFrame, event_date: date) -> Optional[dict]:
    """
    Compute pre-event market features.

    Bias rule: use only rows with date < event_date (strictly prior).
    Reference = last row in that subset (T-1 trading day).

    Returns None when insufficient history exists (e.g. events near
    the start of the price series).
    """
    event_dt = pd.Timestamp(event_date)

    # Rows strictly before event date — enforces look-ahead bias prevention
    prior = prices[prices["date"] < event_dt].reset_index(drop=True)

    if len(prior) < MIN_HISTORY_ROWS:
        return None

    ref = len(prior) - 1   # index of T-1 (most recent available trading day)

    # ── Pre-event cumulative returns ───────────────────────────────────────
    # Pattern: (close[ref] - close[ref - n]) / close[ref - n]
    # These capture momentum/drift in the n days before the announcement.
    def cum_ret(lookback: int) -> Optional[float]:
        idx = ref - lookback
        if idx < 0:
            return None
        p_start = prior.at[idx, "adjusted_close"]
        p_end   = prior.at[ref, "adjusted_close"]
        return float((p_end - p_start) / p_start) if p_start and p_start != 0 else None

    return_1d  = cum_ret(1)    # momentum of the prior trading day
    return_5d  = cum_ret(5)    # 1-week pre-event drift
    return_20d = cum_ret(20)   # 1-month pre-event drift

    # ── Volume z-score ─────────────────────────────────────────────────────
    # Measures whether volume on T-1 was abnormal vs the 20-day baseline.
    # Elevated pre-event volume can signal information leakage.
    vol_window = prior["volume"].iloc[max(0, ref - 19) : ref + 1]
    vol_mean   = vol_window.mean()
    vol_std    = vol_window.std()
    volume_zscore = (
        float((prior.at[ref, "volume"] - vol_mean) / vol_std)
        if vol_std and vol_std > 0
        else 0.0
    )

    # ── Realized volatility (20-day, annualized) ───────────────────────────
    # High-volatility stocks need larger moves to be signal-meaningful.
    # Model uses this to calibrate expected return thresholds.
    ret_window   = prior["daily_return"].iloc[max(0, ref - 19) : ref + 1].dropna()
    volatility_20d = (
        float(ret_window.std() * np.sqrt(252))
        if len(ret_window) >= 5
        else None
    )

    return {
        "return_1d":      return_1d,
        "return_5d":      return_5d,
        "return_20d":     return_20d,
        "volume_zscore":  volume_zscore,
        "volatility_20d": volatility_20d,
    }


# ── Entry point ────────────────────────────────────────────────────────────────

def run() -> None:
    db = SessionLocal()
    try:
        tickers = db.scalars(
            select(Company.ticker).where(Company.is_active == True)
        ).all()
        logger.info("Tickers: %d", len(tickers))

        # Idempotency: skip events already computed for this version
        existing = set(
            db.scalars(
                select(Feature.event_id)
                .where(Feature.feature_version == FEATURE_VERSION)
            ).all()
        )
        logger.info("Already computed: %d", len(existing))

        total_created = total_skipped = total_no_price = 0
        BATCH = 300

        for ticker in tickers:
            prices = load_prices(db, ticker)
            if prices.empty:
                logger.warning("[%s] no price history", ticker)
                continue

            events = db.scalars(
                select(Event)
                .where(Event.ticker == ticker)
                .where(Event.event_type.not_in(EXCLUDE_TYPES))
                .where(Event.event_timestamp.isnot(None))
                .order_by(Event.event_timestamp)
            ).all()

            created = no_price = 0

            for ev in events:
                if ev.id in existing:
                    total_skipped += 1
                    continue

                mf = compute_market_features(prices, ev.event_timestamp.date())

                if mf is None:
                    no_price += 1
                    total_no_price += 1
                    continue

                db.add(Feature(
                    event_id=ev.id,
                    feature_version=FEATURE_VERSION,
                    # Market context
                    return_1d=mf["return_1d"],
                    return_5d=mf["return_5d"],
                    return_20d=mf["return_20d"],
                    volume_zscore=mf["volume_zscore"],
                    volatility_20d=mf["volatility_20d"],
                    # Text and source features populated in next pass
                ))
                created += 1
                total_created += 1

                if total_created % BATCH == 0:
                    db.commit()

            db.commit()
            logger.info("[%s] created=%d  no_price=%d", ticker, created, no_price)

        logger.info(
            "Done. created=%d  skipped=%d  no_price=%d",
            total_created, total_skipped, total_no_price,
        )

    finally:
        db.close()


if __name__ == "__main__":
    run()