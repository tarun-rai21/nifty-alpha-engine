# app/pipelines/build_training_matrix.py
"""
Assembles the flat training matrix from:
  - features table  (market features + event history + labels)
  - events table    (ticker, event_type, event_date)
  - pdf_extracts    (text_features_json, LEFT JOIN — sparse)

Output: /app/data/training_matrix.parquet

Splits:
  train  → event_date.year < 2025
  val    → event_date.year == 2025
  test   → event_date.year >= 2026
"""

import os
import json
import logging
import pandas as pd
from sqlalchemy import text
from app.db import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR  = "/app/data"
OUTPUT_PATH = f"{OUTPUT_DIR}/training_matrix.parquet"

# All 14 event types — must match exactly what's in the DB
EVENT_TYPES = [
    "update", "investor_presentation", "corporate_action", "management_change",
    "shareholder_meeting", "board_meeting", "merger_acquisition", "disclosure",
    "other", "results", "litigation", "news_verification", "rating_change", "agreement",
]

# Default values when a row has no PDF extract (82% of rows)
TEXT_FEATURE_DEFAULTS = {
    "has_pat_growth":         0,
    "has_revenue_growth":     0,
    "has_margin_improvement": 0,
    "has_dividend":           0,
    "has_buyback":            0,
    "has_loss_flag":          0,
    "has_warning_flag":       0,
    "text_sentiment_score":   0.0,   # named differently from features.sentiment_score
}


def unpack_text_features(tf_json) -> dict:
    """Unpack text_features_json JSONB into flat dict. Returns defaults if NULL."""
    if tf_json is None:
        return TEXT_FEATURE_DEFAULTS.copy()
    if isinstance(tf_json, str):
        tf_json = json.loads(tf_json)
    result = TEXT_FEATURE_DEFAULTS.copy()
    for k, v in tf_json.items():
        # Map DB key → output key (rename sentiment_score to avoid collision)
        out_key = "text_sentiment_score" if k == "sentiment_score" else k
        result[out_key] = int(v) if isinstance(v, bool) else v
    return result


def assign_split(year: pd.Series) -> pd.Series:
    splits = pd.Series("train", index=year.index)
    splits[year == 2025] = "val"
    splits[year >= 2026] = "test"
    return splits


def run() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    db = SessionLocal()
    try:
        logger.info("Querying feature matrix from DB...")
        rows = db.execute(text("""
            SELECT
                f.event_id,
                e.ticker,
                e.event_type,
                e.event_timestamp::date        AS event_date,

                -- Market features
                f.return_1d,
                f.return_5d,
                f.return_20d,
                f.volume_zscore,
                f.volatility_20d,

                -- Event history features
                f.similar_event_count_90d,
                f.avg_forward_return_similar,
                f.confirmation_count,

                -- Text features (NULL for ~82% of rows — LEFT JOIN)
                pe.text_features_json,

                -- Labels
                f.y_1d,
                f.y_3d,
                f.y_5d,
                f.y_10d

            FROM features f
            JOIN events e ON e.id = f.event_id
            LEFT JOIN pdf_extracts pe ON pe.event_id = f.event_id
            WHERE f.y_5d IS NOT NULL
            ORDER BY e.event_timestamp
        """)).fetchall()
    finally:
        db.close()

    logger.info(f"Fetched {len(rows)} rows with y_5d labels")

    df = pd.DataFrame(rows, columns=[
        "event_id", "ticker", "event_type", "event_date",
        "return_1d", "return_5d", "return_20d", "volume_zscore", "volatility_20d",
        "similar_event_count_90d", "avg_forward_return_similar", "confirmation_count",
        "text_features_json",
        "y_1d", "y_3d", "y_5d", "y_10d",
    ])

    df["event_date"] = pd.to_datetime(df["event_date"])

    # --- Unpack text features (handles NULL → defaults) ---
    text_df = df["text_features_json"].apply(unpack_text_features).apply(pd.Series)
    df = pd.concat([df.drop(columns=["text_features_json"]), text_df], axis=1)
    bool_text_cols = [
        "has_pat_growth", "has_revenue_growth", "has_margin_improvement",
        "has_dividend", "has_buyback", "has_loss_flag", "has_warning_flag",
    ]
    df[bool_text_cols] = df[bool_text_cols].astype("int8")

    # --- One-hot encode event_type ---
    for et in EVENT_TYPES:
        df[f"event_{et}"] = (df["event_type"] == et).astype("int8")

    # --- Chronological split ---
    df["split"] = assign_split(df["event_date"].dt.year)
    # --- Drop label outliers (data errors: >100% or <-100% 5-day return is impossible on NSE) ---
    n_before = len(df)
    df = df[df["y_5d"].abs() <= 1.0].reset_index(drop=True)
    logger.info(f"Dropped {n_before - len(df)} outlier rows (|y_5d| > 1.0) — likely adj_close errors")

    # --- Feature column list (used for training, not metadata) ---
    meta_cols    = ["event_id", "ticker", "event_type", "event_date", "split"]
    label_cols   = ["y_1d", "y_3d", "y_5d", "y_10d"]
    feature_cols = [c for c in df.columns if c not in meta_cols + label_cols]

    # --- Summary ---
    logger.info("=" * 60)
    logger.info(f"Total rows:{len(df)}")
    logger.info(f"Feature columns:{len(feature_cols)}")
    logger.info(f"\nSplit distribution:\n{df['split'].value_counts().sort_index().to_string()}")
    logger.info(f"\nEvent type distribution:\n{df['event_type'].value_counts().to_string()}")

    has_text = (df[bool_text_cols].any(axis=1)).sum()
    logger.info(f"\nRows with any PDF signal: {has_text} / {len(df)} ({100*has_text/len(df):.1f}%)")

    for split in ["train", "val", "test"]:
        sub = df[df["split"] == split]["y_5d"]
        logger.info(f"y_5d [{split}]:  n={len(sub):>5}  mean={sub.mean():+.4f}  std={sub.std():.4f}")

    logger.info("=" * 60)
    logger.info(f"Feature columns: {feature_cols}")

    # --- Save ---
    df.to_parquet(OUTPUT_PATH, index=False)
    logger.info(f"\nSaved → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()