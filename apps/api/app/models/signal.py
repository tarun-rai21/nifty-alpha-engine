import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    score: Mapped[Optional[float]] = mapped_column(Float)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)
    rank: Mapped[Optional[int]] = mapped_column(Integer)
    direction: Mapped[Optional[str]] = mapped_column(String(10))
    expected_horizon: Mapped[Optional[str]] = mapped_column(String(5))
    explanation_text: Mapped[Optional[str]] = mapped_column(Text)
    model_version: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    generated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    label_forward_1d: Mapped[Optional[float]] = mapped_column(Float)
    label_forward_3d: Mapped[Optional[float]] = mapped_column(Float)
    label_forward_5d: Mapped[Optional[float]] = mapped_column(Float)
    label_forward_10d: Mapped[Optional[float]] = mapped_column(Float)