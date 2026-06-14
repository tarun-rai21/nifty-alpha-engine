"""
ETL transform: raw_documents → events table.
Processes NSE announcements only. News stays in raw_documents.

Decisions:
- event_type: mapped from NSE's 'desc' field (rules-based, not ML)
- materiality_score: rule-assigned by event type
- source_trust_score: 1.0 for NSE (authoritative)
- dedup_group_id: left null for now (clustering comes in feature layer)
"""
import json
import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.company import Company
from app.models.event import Event
from app.models.raw_document import RawDocument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── Event taxonomy ─────────────────────────────────────────────────────────────
# Maps lowercase substrings of NSE's 'desc' field → canonical event_type.
# Order matters: more specific patterns should come first.

EVENT_TYPE_MAP: list[tuple[str, str]] = [
    # ── Results (catch singular "Result" — NSE's actual label) ──────────
    ("financial result",                        "results"),
    ("integrated filing- financial",            "results"),

    # ── Board meetings ───────────────────────────────────────────────────
    ("outcome of board meeting",                "board_meeting"),
    ("board meeting",                           "board_meeting"),

    # ── Corporate actions ────────────────────────────────────────────────
    ("esop",                                    "corporate_action"),
    ("esos",                                    "corporate_action"),
    ("esps",                                    "corporate_action"),
    ("sbeb",                                    "corporate_action"),
    ("allotment of securities",                 "corporate_action"),
    ("issue of securities",                     "corporate_action"),
    ("dividend",                                "corporate_action"),
    ("record date",                             "corporate_action"),
    ("book closure",                            "corporate_action"),
    ("buyback",                                 "corporate_action"),
    ("rights issue",                            "corporate_action"),

    # ── Management changes ───────────────────────────────────────────────
    ("change in management",                    "management_change"),
    ("change in director",                      "management_change"),
    ("appointment",                             "management_change"),
    ("cessation",                               "management_change"),
    ("resignation",                             "management_change"),
    ("re-appointment",                          "management_change"),
    ("retirement",                              "management_change"),
    ("change in auditors",                      "management_change"),

    # ── M&A ──────────────────────────────────────────────────────────────
    ("amalgamation",                            "merger_acquisition"),
    ("merger",                                  "merger_acquisition"),
    ("scheme of arrangement",                   "merger_acquisition"),
    ("acquisition",                             "merger_acquisition"),
    ("disinvestment",                           "merger_acquisition"),
    ("diversification",                         "merger_acquisition"),
    ("sale or disposal",                        "merger_acquisition"),

    # ── Investor meetings (specific patterns first) ───────────────────────
    ("analysts/institutional investor meet",    "investor_presentation"),
    ("investor presentation",                   "investor_presentation"),
    ("analyst meet",                            "investor_presentation"),

    # ── Shareholder meetings ─────────────────────────────────────────────
    ("shareholders meeting",                    "shareholder_meeting"),
    ("annual general meeting",                  "shareholder_meeting"),
    ("postal ballot",                           "shareholder_meeting"),
    ("extraordinary general",                   "shareholder_meeting"),

    # ── Rating ───────────────────────────────────────────────────────────
    ("credit rating",                           "rating_change"),
    ("rating",                                  "rating_change"),

    # ── Litigation ───────────────────────────────────────────────────────
    ("pendency of litigation",                  "litigation"),
    ("action(s) taken",                         "litigation"),
    ("action(s) initiated",                     "litigation"),
    ("orders passed",                           "litigation"),
    ("arbitration",                             "litigation"),
    ("litigation",                              "litigation"),

    # ── News/rumour verification (high value — company forced to respond) ─
    ("rumour verification",                     "news_verification"),
    ("news verification",                       "news_verification"),

    # ── Agreements / disclosures ─────────────────────────────────────────
    ("memorandum of understanding",             "agreement"),
    ("agreements",                              "agreement"),
    ("related party",                           "disclosure"),
    ("disclosure under sebi takeover",          "disclosure"),
    ("disclosure of other upsi",                "disclosure"),

    # ── Administrative noise (zero signal — excluded from feature pipeline)
    ("loss of share certificate",               "administrative"),
    ("copy of newspaper",                       "administrative"),
    ("newspaper advertisement",                 "administrative"),
    ("trading window",                          "administrative"),
    ("certificate under sebi",                  "administrative"),
    ("closure of trading window",               "administrative"),
    ("secretarial compliance",                  "administrative"),
    ("communication to shareholders",           "administrative"),
    ("duplicate share certificate",             "administrative"),

    # ── Updates (catch-all) ──────────────────────────────────────────────
    ("press release",                           "update"),
    ("monthly business updates",                "update"),
    ("general updates",                         "update"),
    ("updates",                                 "update"),
    ("intimation",                              "update"),
]

MATERIALITY: dict[str, float] = {
    "merger_acquisition":    0.95,
    "results":               0.90,
    "news_verification":     0.75,   # company forced to confirm/deny rumour
    "corporate_action":      0.80,
    "management_change":     0.75,
    "rating_change":         0.78,
    "litigation":            0.65,
    "board_meeting":         0.60,
    "agreement":             0.60,
    "shareholder_meeting":   0.50,
    "investor_presentation": 0.35,
    "disclosure":            0.30,
    "update":                0.20,
    "administrative":        0.00,   # excluded from model training
    "other":                 0.15,
}


def classify(desc: str) -> tuple[str, float]:
    """Map NSE desc string → (event_type, materiality_score)."""
    desc_lower = (desc or "").lower().strip()
    for pattern, etype in EVENT_TYPE_MAP:
        if pattern in desc_lower:
            return etype, MATERIALITY[etype]
    return "other", MATERIALITY["other"]


# ── Core transform ─────────────────────────────────────────────────────────────

def run() -> None:
    db = SessionLocal()
    try:
        # Ticker → company_id lookup (avoid per-row queries)
        ticker_to_cid: dict[str, int] = {
            row.ticker: row.id
            for row in db.execute(select(Company.ticker, Company.id)).fetchall()
        }

        # Raw doc IDs already promoted to events (for idempotency)
        existing_doc_ids: set[int] = set(
            db.scalars(
                select(Event.document_id).where(Event.document_id.isnot(None))
            ).all()
        )
        logger.info("Already normalized: %d events", len(existing_doc_ids))

        # Fetch all NSE announcement docs
        raw_docs = db.scalars(
            select(RawDocument).where(RawDocument.source_type == "nse_announcement")
        ).all()
        logger.info("Raw NSE announcement docs: %d", len(raw_docs))

        created = skipped_dup = skipped_no_ticker = 0
        BATCH = 500

        for doc in raw_docs:
            if doc.id in existing_doc_ids:
                skipped_dup += 1
                continue

            if not doc.ticker:
                skipped_no_ticker += 1
                continue

            # Parse stored JSON payload
            payload: dict = {}
            if doc.raw_html:
                try:
                    payload = json.loads(doc.raw_html)
                except (json.JSONDecodeError, TypeError):
                    pass

            desc = payload.get("desc", "")
            event_type, materiality = classify(desc)

            db.add(Event(
                document_id=doc.id,
                ticker=doc.ticker,
                company_id=ticker_to_cid.get(doc.ticker),
                event_type=event_type,
                event_subtype=desc.strip() or None,   # preserves raw NSE label
                event_timestamp=doc.published_at,
                published_at=doc.published_at,
                source_name=doc.source_name,
                source_trust_score=1.0,                # NSE is authoritative
                clean_text=(doc.raw_text or "").strip(),
                language="en",
                materiality_score=materiality,
            ))
            created += 1

            if created % BATCH == 0:
                db.commit()
                logger.info("  committed %d events...", created)

        db.commit()

        total = db.scalar(select(func.count()).select_from(Event))
        logger.info(
            "Done. created=%d  skipped_dup=%d  skipped_no_ticker=%d  total_events=%d",
            created, skipped_dup, skipped_no_ticker, total
        )

    finally:
        db.close()


if __name__ == "__main__":
    run()