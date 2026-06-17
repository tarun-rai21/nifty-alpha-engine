"""
Recompute kv_pairs for existing pdf_extracts without refetching PDFs.
Primary strategy: regex over full_text for percentage-change statements.
Secondary: table extraction only for clean, unambiguous P&L rows.
"""
import logging
import re
import json
from sqlalchemy import text
from app.db import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def extract_kv_from_text(full_text: str) -> dict:
    kv = {}
    if not full_text:
        return kv

    patterns = {
        "revenue_yoy_pct": r"(?:total income|revenue from operations|total revenue)[^.]{0,80}?(increased|decreased|grew|declined|up|down)\s+by\s+(\d+(?:\.\d+)?)\s*%",
        "ebitda_yoy_pct": r"ebid?ta[^.]{0,80}?(increased|decreased|grew|declined|up|down)\s+by\s+(\d+(?:\.\d+)?)\s*%",
        "pat_yoy_pct": r"(?:pat|profit after tax|net profit|attributable pat)[^.]{0,80}?(increased|decreased|grew|declined|up|down)\s+by\s+(\d+(?:\.\d+)?)\s*%",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m:
            direction = 1 if m.group(1).lower() in ("increased", "grew", "up") else -1
            kv[key] = direction * float(m.group(2)) / 100

    return kv


def extract_kv_table_positional(tables: list) -> dict:
    kv = {}
    label_map = {
        "revenue from operations": "revenue_current",
        "total income": "total_income_current",
        "profit for the period": "pat_current",
        "profit after tax": "pat_current",
        "net profit": "pat_current",
    }

    for table in tables:
        for row in table:
            if not row or len(row) < 2:
                continue
            # find a cell that looks like a multi-line label block
            label_cell = None
            label_idx = None
            for i, cell in enumerate(row):
                if cell and cell.count("\n") > 3 and any(
                    k in cell.lower() for k in label_map
                ):
                    label_cell = cell
                    label_idx = i
                    break
            if label_cell is None:
                continue

            labels = [l.strip().lower() for l in label_cell.split("\n")]

            # find the first numeric multi-line cell after label_idx
            value_cell = None
            for cell in row[label_idx + 1:]:
                if cell and cell.count("\n") > 3:
                    value_cell = cell
                    break
            if value_cell is None:
                continue

            values = [v.strip() for v in value_cell.split("\n")]

            for pattern, key in label_map.items():
                if key in kv:
                    continue
                for li, lab in enumerate(labels):
                    if pattern in lab and li < len(values):
                        val = parse_number(values[li])
                        if val is not None:
                            kv[key] = val
                            break
    return kv


def parse_number(val) -> float | None:
    if not val:
        return None
    val = str(val).strip().replace(",", "").replace(" ", "")
    negative = val.startswith("(") and val.endswith(")")
    val = val.strip("()")
    try:
        n = float(val)
        return -n if negative else n
    except ValueError:
        return None


def run():
    db = SessionLocal()
    rows = db.execute(text("""
        SELECT pe.id, pe.full_text, pe.tables_json, e.event_type
        FROM pdf_extracts pe
        JOIN events e ON e.id = pe.event_id
        WHERE pe.extraction_status = 'success'
    """)).fetchall()

    logger.info("Reprocessing %d extracts", len(rows))
    updated = 0

    for extract_id, full_text, tables_json, event_type in rows:
        kv = extract_kv_from_text(full_text)

        if event_type == "results" and tables_json:
            table_kv = extract_kv_table_positional(tables_json)
            kv.update(table_kv)

        db.execute(
            text("UPDATE pdf_extracts SET kv_pairs = :kv WHERE id = :id"),
            {"kv": json.dumps(kv), "id": extract_id}
        )
        updated += 1
        if updated % 500 == 0:
            db.commit()
            logger.info("Progress: %d / %d", updated, len(rows))

    db.commit()
    logger.info("Done — updated %d extracts", updated)
    db.close()


if __name__ == "__main__":
    run()