import uuid

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class EventEntity(Base):
    __tablename__ = "event_entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )

    entity_type = Column(String(50), nullable=False)       # ORG, PERSON, MONEY, DATE, PERCENT
    entity_text = Column(String(500), nullable=False)      # raw text as extracted by NER
    entity_normalized = Column(String(500), nullable=True) # cleaned form e.g. "HDFC Bank Ltd." → "HDFCBANK"
    confidence = Column(Float, nullable=True)              # spaCy NER confidence score

    # Character offsets in source text — useful for NER debugging
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_event_entities_event_id", "event_id"),
        Index("ix_event_entities_entity_type", "entity_type"),
    )