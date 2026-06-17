from sqlalchemy import Column, Integer, String, Text, JSON, ForeignKey, DateTime
from sqlalchemy.sql import func
from app.db import Base


class PdfExtract(Base):
    __tablename__ = "pdf_extracts"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), unique=True, nullable=False)
    raw_document_id = Column(Integer, ForeignKey("raw_documents.id"), nullable=False)

    # Text content
    full_text = Column(Text)  # all text, concatenated by page
    press_release_text = Column(Text)  # Annexure B if results, else full_text

    # Structured extraction
    tables_json = Column(JSON)  # list of tables, each as list of rows
    kv_pairs = Column(JSON)  # extracted key-value: {"revenue_yoy": 0.202, ...}

    # Metadata
    page_count = Column(Integer)
    extraction_status = Column(String(50))  # "success", "failed", "skipped"
    extraction_error = Column(Text)
    created_at = Column(DateTime, server_default=func.now())