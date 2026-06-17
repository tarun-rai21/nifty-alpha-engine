from app.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()

print("\n=== raw tables_json for one ICICIBANK results event ===")
row = db.execute(text("""
    SELECT pe.tables_json, pe.kv_pairs
    FROM pdf_extracts pe
    JOIN events e ON e.id = pe.event_id
    WHERE e.event_type = 'results'
    AND e.ticker = 'ICICIBANK'
    AND pe.extraction_status = 'success'
    LIMIT 1
""")).fetchone()

import json

tables = row[0] if row[0] else []
print(f"Number of tables: {len(tables)}")
for i, t in enumerate(tables[:3]):
    print(f"\n--- Table {i} ---")
    print(json.dumps(t, indent=2)[:2000])

print("\n=== raw tables_json for non-bank results event ===")
row = db.execute(text("""
    SELECT e.ticker, pe.tables_json
    FROM pdf_extracts pe
    JOIN events e ON e.id = pe.event_id
    WHERE e.event_type = 'results'
    AND e.ticker NOT IN ('ICICIBANK','HDFCBANK','KOTAKBANK','AXISBANK','SBIN','INDUSINDBK','FEDERALBNK','BANDHANBNK')
    AND pe.kv_pairs::text != '{}'
    LIMIT 1
""")).fetchone()

if row:
    tables = row[1] if row[1] else []
    print(f"Ticker: {row[0]}, tables: {len(tables)}")
    for i, t in enumerate(tables[:2]):
        print(f"\n--- Table {i} ---")
        print(json.dumps(t, indent=2)[:2000])

db.close()