"""
Phase 4 — XGBoost baseline training (non-NLP features only).

Target:  y_5d  (5-day forward return, regression)
Split:   chronological — train < 2025, val 2025, test >= 2026
Primary metric: Spearman IC (Information Coefficient)

IC interpretation:
    < 0.02  → no signal, noise
    0.02–0.05 → weak but real signal (typical for single-factor models)
    0.05–0.10 → solid for 7 features with no NLP
    > 0.10  → re-examine for leakage before celebrating

XGBoost handles NaN natively. avg_forward_return_similar is null
for early events with no prior history — do not impute.
"""
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import select
import xgboost as xgb

from app.db import SessionLocal
from app.models.event import Event
from app.models.feature import Feature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_DIR = Path("/app/models")
MODEL_DIR.mkdir(exist_ok=True)

FEATURE_VERSION = "v1"
FEATURE_COLS = [
    "return_1d",
    "return_5d",
    "return_20d",
    "volume_zscore",
    "volatility_20d",
    "similar_event_count_90d",
    "avg_forward_return_similar",
    "event_type_enc",
]
TARGET = "y_5d"

TRAIN_END = "2025-01-01"
VAL_END   = "2026-01-01"


# ── Data loading ───────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                Event.ticker,
                Event.event_type,
                Event.event_timestamp,
                Feature.return_1d,
                Feature.return_5d,
                Feature.return_20d,
                Feature.volume_zscore,
                Feature.volatility_20d,
                Feature.similar_event_count_90d,
                Feature.avg_forward_return_similar,
                Feature.y_1d,
                Feature.y_3d,
                Feature.y_5d,
                Feature.y_10d,
            )
            .join(Feature, Feature.event_id == Event.id)
            .where(Feature.feature_version == FEATURE_VERSION)
            .where(Feature.y_5d.isnot(None))
            .where(Event.event_type != "administrative")
        ).fetchall()
    finally:
        db.close()

    df = pd.DataFrame(rows, columns=[
        "ticker", "event_type", "event_timestamp",
        "return_1d", "return_5d", "return_20d",
        "volume_zscore", "volatility_20d",
        "similar_event_count_90d", "avg_forward_return_similar",
        "y_1d", "y_3d", "y_5d", "y_10d",
    ])
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"])
    return df.sort_values("event_timestamp").reset_index(drop=True)


# ── Feature prep ───────────────────────────────────────────────────────────────

def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    # Ordinal encode event_type
    # Do NOT one-hot — XGBoost splits on ordinal values directly,
    # and one-hot on 14 categories adds noise columns with no gain
    type_map = {t: i for i, t in enumerate(sorted(df["event_type"].unique()))}
    df["event_type_enc"] = df["event_type"].map(type_map)
    logger.info("Event type encoding: %s", type_map)
    return df


# ── Evaluation ─────────────────────────────────────────────────────────────────

def evaluate(name: str, y_true: np.ndarray, y_pred: np.ndarray) -> None:
    ic, pval = stats.spearmanr(y_true, y_pred)
    dir_acc  = np.mean(np.sign(y_pred) == np.sign(y_true))
    rmse     = np.sqrt(np.mean((y_true - y_pred) ** 2))

    # Quintile spread: does top-quintile predicted return beat bottom-quintile actual?
    # This is the real trading signal quality test, not RMSE.
    df_eval   = pd.DataFrame({"y_true": y_true, "y_pred": y_pred})
    df_eval["quintile"] = pd.qcut(df_eval["y_pred"], q=5, labels=[1, 2, 3, 4, 5])
    q_returns = df_eval.groupby("quintile")["y_true"].mean().round(4)
    spread    = q_returns[5] - q_returns[1]   # Q5 - Q1: the long/short spread

    logger.info("── %s ──────────────────────────────", name)
    logger.info("  IC           : %.4f  (p=%.4f)", ic, pval)
    logger.info("  Dir accuracy : %.3f", dir_acc)
    logger.info("  RMSE         : %.6f", rmse)
    logger.info("  Quintile mean y_5d:")
    for q, r in q_returns.items():
        logger.info("    Q%s: %+.4f", q, r)
    logger.info("  Q5-Q1 spread : %+.4f", spread)


# ── Training ───────────────────────────────────────────────────────────────────

def run() -> None:
    logger.info("Loading data...")
    df = load_data()
    df = prepare_features(df)

    logger.info("Samples with y_5d: %d", len(df))
    logger.info(
        "Date range: %s → %s",
        df["event_timestamp"].min().date(),
        df["event_timestamp"].max().date(),
    )

    train = df[df["event_timestamp"] <  TRAIN_END]
    val   = df[(df["event_timestamp"] >= TRAIN_END) & (df["event_timestamp"] < VAL_END)]
    test  = df[df["event_timestamp"] >= VAL_END]

    logger.info("Split — train: %d | val: %d | test: %d", len(train), len(val), len(test))

    X_train, y_train = train[FEATURE_COLS].values, train[TARGET].values
    X_val,   y_val   = val[FEATURE_COLS].values,   val[TARGET].values
    X_test,  y_test  = test[FEATURE_COLS].values,  test[TARGET].values

    model = xgb.XGBRegressor(
        objective             = "reg:squarederror",
        n_estimators          = 1000,
        max_depth             = 4,      # shallow — ~27k samples, 7 features, overfit risk is real
        learning_rate         = 0.05,
        subsample             = 0.8,
        colsample_bytree      = 0.8,
        min_child_weight      = 20,     # leaf must cover 20+ events — prevents micro-splits
        gamma                 = 1.0,    # minimum gain required to split
        reg_lambda            = 2.0,    # L2 regularization on leaf weights
        early_stopping_rounds = 50,
        eval_metric           = "rmse",
        random_state          = 42,
        n_jobs                = -1,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=100,
    )

    logger.info("Best iteration: %d", model.best_iteration)

    evaluate("VAL",  y_val,  model.predict(X_val))
    evaluate("TEST", y_test, model.predict(X_test))

    # Feature importance by gain — directionally useful, not definitive
    # (gain overstates high-cardinality features; SHAP is more reliable but later)
    importance = sorted(
        zip(FEATURE_COLS, model.feature_importances_),
        key=lambda x: -x[1]
    )
    logger.info("Feature importance (gain):")
    for feat, imp in importance:
        logger.info("  %-35s %.4f", feat, imp)

    model.save_model(MODEL_DIR / "xgb_baseline_v1.json")
    logger.info("Model saved → /app/models/xgb_baseline_v1.json")


if __name__ == "__main__":
    run()