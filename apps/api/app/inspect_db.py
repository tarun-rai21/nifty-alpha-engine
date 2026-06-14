from app.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# add to inspect_db.py
print("\n=== PDF URLs by event type ===")
rows = db.execute(text("""
    SELECT e.event_type, rd.source_url
    FROM events e
    JOIN raw_documents rd ON rd.id = e.document_id
    WHERE rd.source_url LIKE '%.pdf'
    AND e.event_type IN ('results', 'merger_acquisition', 'rating_change', 'management_change')
    GROUP BY e.event_type, rd.source_url
    LIMIT 8
""")).fetchall()
for r in rows: print(r)

db.close()