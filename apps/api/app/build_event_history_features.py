"""
Phase 3c — Event history feature generation.
Populates similar_event_count_90d and avg_forward_return_similar.

Look-ahead rule: prior event E' is only used if
    date_plus_n_trading_days(calendar, E'.event_date, 10) < E.event_date
i.e., E's full 10-day return window elapsed before E occurred.

avg_forward_return_similar anchors on y_5d (5-day forward return).
Rationale: y_1d is noise-dominated; y_10d excludes too many recent events.
y_5d is the practical medium-term signal horizon.
"""
import bisect
import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models.company import Company
from app.models.event import Event
from app.models.feature import Feature
from app.models.price_history import PriceHistory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FEATURE_VERSION  = "v1"
EXCLUDE_TYPES    = {"administrative"}
LOOKBACK_DAYS    = 90
LABEL_HORIZON    = 10   # trading days that must elapse before a prior event's return is usable
BATCH            = 300


def load_trading_calendar(db, ticker: str) -> list[date]:
    rows = db.execute(
        select(PriceHistory.date)
        .where(PriceHistory.ticker == ticker)
        .order_by(PriceHistory.date)
    ).scalars().all()
    return [r if isinstance(r, date) else r.date() for r in rows]


def date_plus_n_trading_days(
    calendar: list[date], from_date: date, n: int
) -> Optional[date]:
    """
    Returns the calendar date n trading days on or after from_date.
    Uses bisect so it handles non-trading event dates correctly.
    Returns None when n steps would go past the end of the calendar.
    """
    idx    = bisect.bisect_left(calendar, from_date)
    target = idx + n
    return calendar[target] if target < len(calendar) else None


def run() -> None:
    db = SessionLocal()
    try:
        tickers = db.scalars(
            select(Company.ticker).where(Company.is_active == True)
        ).all()
        logger.info("Tickers: %d", len(tickers))

        total_updated = 0
        batch_updates = []

        for ticker in tickers:
            calendar = load_trading_calendar(db, ticker)
            if not calendar:
                logger.warning("[%s] no trading calendar", ticker)
                continue

            # Single query: events joined to their feature row for this ticker
            rows = db.execute(
                select(
                    Event.id.label("event_id"),
                    Event.event_type,
                    Event.event_timestamp,
                    Feature.id.label("feature_id"),
                    Feature.y_5d,
                )
                .join(Feature, Feature.event_id == Event.id)
                .where(Event.ticker          == ticker)
                .where(Event.event_type.not_in(EXCLUDE_TYPES))
                .where(Event.event_timestamp.isnot(None))
                .where(Feature.feature_version == FEATURE_VERSION)
                .order_by(Event.event_timestamp)
            ).fetchall()

            if not rows:
                continue

            # Pre-compute per-event metadata including look-ahead boundary
            events_data = []
            for r in rows:
                ev_date = r.event_timestamp.date()
                events_data.append({
                    "event_id":   r.event_id,
                    "feature_id": r.feature_id,
                    "event_type": r.event_type,
                    "event_date": ev_date,
                    # First date from which this event's 10-day return is observable
                    "known_after": date_plus_n_trading_days(calendar, ev_date, LABEL_HORIZON),
                    "y_5d":       r.y_5d,
                })

            for i, ev in enumerate(events_data):
                current_date = ev["event_date"]
                cutoff_date  = current_date - timedelta(days=LOOKBACK_DAYS)

                # Eligible prior events: same type, in 90-day window,
                # 10-day return fully elapsed before current event
                eligible = [
                    p for p in events_data[:i]   # [:i] guarantees p.event_date <= current
                    if (
                        p["event_type"]  == ev["event_type"]
                        and p["event_date"] >= cutoff_date
                        and p["known_after"] is not None
                        and p["known_after"]  < current_date
                    )
                ]

                count = len(eligible)

                # avg uses only eligible events that have a valid y_5d
                with_return = [p for p in eligible if p["y_5d"] is not None]
                avg_ret = (
                    sum(p["y_5d"] for p in with_return) / len(with_return)
                    if with_return else None
                )

                batch_updates.append({
                    "id":                        ev["feature_id"],
                    "similar_event_count_90d":    count,
                    "avg_forward_return_similar": avg_ret,
                })
                total_updated += 1

                if len(batch_updates) >= BATCH:
                    db.execute(update(Feature), batch_updates)
                    db.commit()
                    batch_updates = []

            logger.info("[%s] processed %d events", ticker, len(events_data))

        if batch_updates:
            db.execute(update(Feature), batch_updates)
            db.commit()

        logger.info("Done. updated=%d", total_updated)

    finally:
        db.close()


if __name__ == "__main__":
    run()