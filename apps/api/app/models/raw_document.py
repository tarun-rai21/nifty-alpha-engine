import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RawDocument(Base):
    __tablename__ = "raw_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    raw_html: Mapped[Optional[str]] = mapped_column(Text)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    ticker: Mapped[Optional[str]] = mapped_column(String(20), index=True)