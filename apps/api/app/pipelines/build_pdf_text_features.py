# app/pipelines/build_pdf_text_features.py

import re
import json
import logging
from app.db import SessionLocal
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pattern library
# Each entry: (regex_pattern, tag)
# Tags are collected into pos_hits / neg_hits sets, then mapped to binary flags.
# ---------------------------------------------------------------------------

POSITIVE_PATTERNS = [
    # PAT / net profit growth
    (r"(?:pat|net profit|profit after tax|profit for (?:the )?(?:period|year|quarter))"
     r"[^.\n]{0,100}(?:grew|increased|improved|higher|up\b|growth|rise|rose|record)\b", "pat"),
    (r"(?:grew|increased|improved|higher|growth|rose|record)\b"
     r"[^.\n]{0,100}(?:pat|net profit|profit after tax)\b", "pat"),

    # Revenue / sales growth
    (r"(?:revenue|sales|turnover|income from operations)"
     r"[^.\n]{0,100}(?:grew|increased|improved|higher|up\b|growth|rise|rose|record)\b", "revenue"),
    (r"(?:grew|increased|improved|higher|growth|rose)\b"
     r"[^.\n]{0,100}(?:revenue|sales|turnover)\b", "revenue"),

    # Margin improvement
    (r"(?:ebitda|operating|gross|net)\s*margin"
     r"[^.\n]{0,100}(?:expanded|improved|increased|higher|improvement|expansion)\b", "margin"),
    (r"margin (?:expansion|improvement)\b", "margin"),

    # Dividend
    (r"(?:interim|final|special)?\s*dividend"
     r"[^.\n]{0,80}(?:declared|announced|recommended|increased|higher|enhanced)\b", "dividend"),
    (r"(?:declared|recommended|announced)[^.\n]{0,80}dividend\b", "dividend"),

    # Buyback
    (r"(?:buyback|buy.back|share repurchase|repurchase of (?:equity )?shares)\b", "buyback"),
]

NEGATIVE_PATTERNS = [
    # Revenue / profit decline
    (r"(?:revenue|sales|turnover|pat|net profit|profit|ebitda)"
     r"[^.\n]{0,100}(?:declined|decreased|fell|dropped|lower|reduced|contraction|de.?growth)\b", "decline"),
    (r"(?:declined|decreased|fell|dropped|lower|reduced|contraction)\b"
     r"[^.\n]{0,100}(?:revenue|sales|profit|pat|ebitda|turnover)\b", "decline"),

    # Net loss
    (r"\bnet loss\b", "loss"),
    (r"\bincurred (?:a )?loss\b", "loss"),
    (r"\bloss for (?:the )?(?:period|year|quarter)\b", "loss"),

    # Margin contraction
    (r"(?:margin|ebitda|operating profit)"
     r"[^.\n]{0,100}(?:contracted|compressed|declined|fell|reduced|lower)\b", "margin_contraction"),

    # Warning / structural flags
    (r"\binvestigation\b", "warning"),
    (r"\bgoing concern\b", "warning"),
    (r"\bimpairment\b", "warning"),
    (r"\bwrite.?(?:off|down)\b", "warning"),
    (r"\bfraud\b", "warning"),

    # Senior management exit
    (r"(?:ceo|md|managing director|chairman|cfo)[^.\n]{0,80}"
     r"(?:resigned|stepped down|quit|vacated)\b", "mgmt_exit"),
]


def extract_text_features(full_text: str, press_release_text: str | None) -> dict:
    """
    Returns a dict of binary signal flags + a [-1, 1] sentiment_score.
    Prefers press_release_text (cleaner prose) over full_text when available.
    Returns {} if no usable text is found.
    """
    text = press_release_text if press_release_text else full_text
    if not text:
        return {}

    text_lower = text.lower()

    pos_hits: set[str] = set()
    neg_hits: set[str] = set()

    for pattern, tag in POSITIVE_PATTERNS:
        if re.search(pattern, text_lower):
            pos_hits.add(tag)

    for pattern, tag in NEGATIVE_PATTERNS:
        if re.search(pattern, text_lower):
            neg_hits.add(tag)

    features = {
        # Growth flags — only fire if not contradicted by a decline tag
        "has_pat_growth": ("pat" in pos_hits) and ("decline" not in neg_hits) and ("loss" not in neg_hits),
        "has_revenue_growth": ("revenue" in pos_hits) and ("decline" not in neg_hits),
        "has_margin_improvement": ("margin" in pos_hits) and ("margin_contraction" not in neg_hits),

        # Corporate action flags
        "has_dividend": "dividend" in pos_hits,
        "has_buyback": "buyback" in pos_hits,

        # Risk / deterioration flags
        "has_loss_flag": ("loss" in neg_hits) or ("decline" in neg_hits and "pat" not in pos_hits),
        "has_warning_flag": bool(neg_hits & {"warning", "mgmt_exit"}),

        # Continuous sentiment in [-1, 1]
        "sentiment_score": round(
            (len(pos_hits) - len(neg_hits)) / (len(pos_hits) + len(neg_hits) + 1), 4
        ),
    }
    return features


def run() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, full_text, press_release_text
            FROM pdf_extracts
            WHERE full_text IS NOT NULL OR press_release_text IS NOT NULL
        """)).fetchall()

        total = len(rows)
        logger.info(f"Processing {total} extracts")

        updated = 0
        skipped = 0

        for row_id, full_text, press_release_text in rows:
            features = extract_text_features(full_text, press_release_text)
            if not features:
                skipped += 1
                continue

            db.execute(
                text("""
                    UPDATE pdf_extracts
                    SET text_features_json = CAST(:features AS jsonb)
                    WHERE id = :id
                """),
                {"features": json.dumps(features), "id": row_id},
            )
            updated += 1

            if updated % 500 == 0:
                db.commit()
                logger.info(f"Progress: {updated} / {total}")

        db.commit()
        logger.info(f"Done — updated {updated}, skipped {skipped}")

    finally:
        db.close()


if __name__ == "__main__":
    run()