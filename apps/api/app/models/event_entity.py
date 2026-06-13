from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EventEntity(Base):
    __tablename__ = "event_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_text: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_normalized: Mapped[Optional[str]] = mapped_column(String(500))
    confidence: Mapped[Optional[float]] = mapped_column(Float)

    char_start: Mapped[Optional[int]] = mapped_column(Integer)
    char_end: Mapped[Optional[int]] = mapped_column(Integer)