import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("raw_documents.id"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.id"), index=True
    )
    event_type: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    event_subtype: Mapped[Optional[str]] = mapped_column(String(50))
    event_timestamp: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    source_name: Mapped[Optional[str]] = mapped_column(String(100))
    source_trust_score: Mapped[Optional[float]] = mapped_column(Float)
    clean_text: Mapped[Optional[str]] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(10), default="en")
    materiality_score: Mapped[Optional[float]] = mapped_column(Float)
    dedup_group_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )