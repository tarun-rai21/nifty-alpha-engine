import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")

    # ── Group 1: Text features ─────────────────────────────────────
    sentiment_score:   Mapped[Optional[float]] = mapped_column(Float)
    novelty_score:     Mapped[Optional[float]] = mapped_column(Float)
    urgency_score:     Mapped[Optional[float]] = mapped_column(Float)
    materiality_score: Mapped[Optional[float]] = mapped_column(Float)

    # ── Group 2: Source / confirmation ────────────────────────────
    source_trust_score: Mapped[Optional[float]] = mapped_column(Float)
    confirmation_count: Mapped[Optional[int]]   = mapped_column(Integer, default=0)

    # ── Group 3: Market context ───────────────────────────────────
    return_1d:      Mapped[Optional[float]] = mapped_column(Float)
    return_5d:      Mapped[Optional[float]] = mapped_column(Float)
    return_20d:     Mapped[Optional[float]] = mapped_column(Float)
    volume_zscore:  Mapped[Optional[float]] = mapped_column(Float)
    volatility_20d: Mapped[Optional[float]] = mapped_column(Float)

    # ── Group 4: Event history ────────────────────────────────────
    similar_event_count_90d:    Mapped[Optional[int]]   = mapped_column(Integer)
    avg_forward_return_similar: Mapped[Optional[float]] = mapped_column(Float)

    # ── Group 5: Forward return labels ───────────────────────────
    # Anchor: adj_close at T-1 (last trading day before event_date).
    # y_nd = adj_close[T-1 + n] / adj_close[T-1] - 1
    # Null when insufficient future price data exists (recent events).
    y_1d:  Mapped[Optional[float]] = mapped_column(Float)
    y_3d:  Mapped[Optional[float]] = mapped_column(Float)
    y_5d:  Mapped[Optional[float]] = mapped_column(Float)
    y_10d: Mapped[Optional[float]] = mapped_column(Float)

    # ── Full feature vector (input to XGBoost) ────────────────────
    feature_vector: Mapped[Optional[dict]] = mapped_column(JSONB)

    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("event_id", "feature_version", name="uq_features_event_version"),
    )