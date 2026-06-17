from app.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()

print("\n=== sample results full_text where kv extraction failed ===")
rows = db.execute(text("""
    SELECT e.ticker, pe.full_text
    FROM pdf_extracts pe
    JOIN events e ON e.id = pe.event_id
    WHERE e.event_type = 'results'
    AND pe.extraction_status = 'success'
    AND (pe.kv_pairs->>'revenue_yoy_pct' IS NULL)
    LIMIT 5
""")).fetchall()

for ticker, text_content in rows:
    print(f"\n--- {ticker} ---")
    # search for any mention of revenue/income/PAT manually
    for keyword in ["revenue", "total income", "profit after tax", "pat", "ebitda"]:
        idx = text_content.lower().find(keyword)
        if idx >= 0:
            print(f"  '{keyword}' found at {idx}: ...{text_content[max(0,idx-50):idx+150]}...")
db.close()