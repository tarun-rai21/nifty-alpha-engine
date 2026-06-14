"""
Phase 3b — Forward return label generation.
Updates existing Feature rows with y_1d, y_3d, y_5d, y_10d.

Anchor: T-1 adjusted_close (last trading day before event_date).
y_nd = adj_close[T-1 + n] / adj_close[T-1] - 1

This is consistent with the feature reference price in build_market_features.
For backtest entry use T+1 open — separate concern from label definition.
"""
import logging
from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.event import Event
from app.models.feature import Feature
from app.models.price_history import PriceHistory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FEATURE_VERSION = "v1"
HORIZONS = {
    "y_1d": 1,
    "y_3d": 3,
    "y_5d": 5,
    "y_10d": 10,
}
MAX_HORIZON = max(HORIZONS.values())


def load_prices(db: Session, ticker: str) -> pd.DataFrame:
    rows = db.execute(
        select(PriceHistory.date, PriceHistory.adjusted_close)
        .where(PriceHistory.ticker == ticker)
        .order_by(PriceHistory.date)
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "adjusted_close"])
    df["date"] = pd.to_datetime(df["date"])
    return df.reset_index(drop=True)


def compute_labels(prices: pd.DataFrame, event_date: date) -> Optional[dict]:
    event_dt = pd.Timestamp(event_date)

    prior = prices[prices["date"] < event_dt].reset_index(drop=True)
    if prior.empty:
        return None

    ref_idx_in_full = prices[prices["date"] < event_dt].index[-1]
    ref_price = prices.at[ref_idx_in_full, "adjusted_close"]

    if not ref_price or ref_price == 0:
        return None

    labels = {}
    for label, n in HORIZONS.items():
        target_idx = ref_idx_in_full + n
        if target_idx >= len(prices):
            labels[label] = None  # Not enough future data yet
        else:
            future_price = prices.at[target_idx, "adjusted_close"]
            labels[label] = float((future_price - ref_price) / ref_price) if future_price else None

    return labels


def run() -> None:
    db = SessionLocal()
    try:
        # Load all Feature rows for this version that need labels
        features = db.execute(
            select(Feature.id, Feature.event_id)
            .where(Feature.feature_version == FEATURE_VERSION)
            .where(Feature.y_1d.is_(None))  # Only rows not yet labelled
        ).fetchall()

        logger.info("Features needing labels: %d", len(features))

        # Map event_id -> feature_id for bulk updates
        event_to_feature = {row.event_id: row.id for row in features}

        # Get event metadata
        event_ids = list(event_to_feature.keys())
        events = db.execute(
            select(Event.id, Event.ticker, Event.event_timestamp)
            .where(Event.id.in_(event_ids))
        ).fetchall()

        # Group by ticker
        from collections import defaultdict
        ticker_events = defaultdict(list)
        for ev in events:
            ticker_events[ev.ticker].append(ev)

        total_updated = total_partial = total_no_future = 0
        BATCH = 300
        batch_updates = []

        for ticker, evs in ticker_events.items():
            prices = load_prices(db, ticker)
            if prices.empty:
                logger.warning("[%s] no price history", ticker)
                continue

            for ev in evs:
                labels = compute_labels(prices, ev.event_timestamp.date())

                if labels is None:
                    total_no_future += 1
                    continue

                has_any = any(v is not None for v in labels.values())
                has_all = all(v is not None for v in labels.values())

                if not has_any:
                    total_no_future += 1
                    continue

                if not has_all:
                    total_partial += 1

                feat_id = event_to_feature[ev.id]
                batch_updates.append({
                    "id": feat_id,
                    **labels,
                })
                total_updated += 1

                if len(batch_updates) >= BATCH:
                    db.execute(update(Feature), batch_updates)
                    db.commit()
                    batch_updates = []

        if batch_updates:
            db.execute(update(Feature), batch_updates)
            db.commit()

        logger.info(
            "Done. updated=%d  partial(some horizons None)=%d  no_future=%d",
            total_updated, total_partial, total_no_future,
        )

    finally:
        db.close()


if __name__ == "__main__":
    run()