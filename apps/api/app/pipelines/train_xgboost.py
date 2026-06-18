# app/pipelines/train_xgboost.py
"""
XGBoost baseline — regression on y_5d

Input:  /app/data/training_matrix.parquet
Output: /app/models/xgboost_v1.json
        /app/models/feature_importance_v1.csv

IC interpretation:
  < 0.02          → noise
  0.02 – 0.05     → weak signal
  0.05 – 0.10     → useful
  > 0.10          → investigate for leakage
"""

import os
import logging
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import xgboost as xgb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PARQUET_PATH = "/app/data/training_matrix.parquet"
MODEL_DIR    = "/app/models"
MODEL_PATH   = f"{MODEL_DIR}/xgboost_v1.json"
FI_PATH      = f"{MODEL_DIR}/feature_importance_v1.csv"

LABEL = "y_5d"

FEATURE_COLS = [
    # Market features (5)
    "return_1d", "return_5d", "return_20d", "volume_zscore", "volatility_20d",
    # Event history features (3)
    "similar_event_count_90d", "avg_forward_return_similar", "confirmation_count",
    # Text features (8)
    "has_pat_growth", "has_revenue_growth", "has_margin_improvement",
    "has_dividend", "has_buyback", "has_loss_flag", "has_warning_flag",
    "text_sentiment_score",
    # Event type one-hot (14)
    "event_update", "event_investor_presentation", "event_corporate_action",
    "event_management_change", "event_shareholder_meeting", "event_board_meeting",
    "event_merger_acquisition", "event_disclosure", "event_other", "event_results",
    "event_litigation", "event_news_verification", "event_rating_change", "event_agreement",
]

XGB_PARAMS = {
    "n_estimators":       500,
    "max_depth":          4,       # back to 4
    "learning_rate":      0.05,
    "subsample":          0.8,
    "colsample_bytree":   0.7,     # was 0.6
    "min_child_weight":   20,      # was 30
    "reg_alpha":          0.3,     # was 0.5
    "reg_lambda":         2.0,     # was 5.0
    "objective":          "reg:squarederror",
    "tree_method":        "hist",
    "random_state":       42,
    "n_jobs":             -1,
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def ic_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Spearman rank IC — measures ranking quality, not magnitude."""
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    if mask.sum() < 10:
        return float("nan")
    return float(spearmanr(y_true[mask], y_pred[mask]).statistic)


def quintile_spread(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Q5 mean return minus Q1 mean return.
    Primary signal quality metric: positive = model ranks events correctly.
    """
    df = pd.DataFrame({"y": y_true, "pred": y_pred}).dropna()
    if len(df) < 50:
        return float("nan")
    df["q"] = pd.qcut(df["pred"], 5, labels=False, duplicates="drop")
    qrets = df.groupby("q")["y"].mean()
    if 4 not in qrets.index or 0 not in qrets.index:
        return float("nan")
    return float(qrets[4] - qrets[0])


def quintile_table(y_true: np.ndarray, y_pred: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame({"y": y_true, "pred": y_pred}).dropna()
    df["q"] = pd.qcut(df["pred"], 5, duplicates="drop")
    n_bins = df["q"].cat.categories.size
    if n_bins < 5:
        logger.warning(f"Only {n_bins} unique prediction bins — predictions nearly constant")
    df["q"] = df["q"].cat.rename_categories([f"Q{i+1}" for i in range(n_bins)])
    return df.groupby("q", observed=True)["y"].agg(["mean", "count"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    os.makedirs(MODEL_DIR, exist_ok=True)

    # --- Load ---
    logger.info(f"Loading {PARQUET_PATH}")
    df = pd.read_parquet(PARQUET_PATH)

    train = df[df["split"] == "train"].copy()
    val   = df[df["split"] == "val"].copy()
    test  = df[df["split"] == "test"].copy()

    logger.info(f"Train: {len(train):,} | Val: {len(val):,} | Test: {len(test):,}")

    # --- NaN imputation: fit on train, apply to all splits ---
    medians = train[FEATURE_COLS].median()
    nan_counts = train[FEATURE_COLS].isna().sum()
    if nan_counts.any():
        logger.info(f"NaN counts in train:\n{nan_counts[nan_counts > 0].to_string()}")

    train[FEATURE_COLS] = train[FEATURE_COLS].fillna(medians)
    val[FEATURE_COLS]   = val[FEATURE_COLS].fillna(medians)
    test[FEATURE_COLS]  = test[FEATURE_COLS].fillna(medians)

    X_train, y_train = train[FEATURE_COLS].values, train[LABEL].values
    X_val,   y_val   = val[FEATURE_COLS].values,   val[LABEL].values
    X_test,  y_test  = test[FEATURE_COLS].values,  test[LABEL].values

    # --- Train ---
    logger.info("Training XGBoost (early stopping on val RMSE)...")
    model = xgb.XGBRegressor(
        **XGB_PARAMS,
        early_stopping_rounds=50,
        eval_metric="rmse",
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=50,
    )
    logger.info(f"Best iteration: {model.best_iteration} / {XGB_PARAMS['n_estimators']}")

    # --- Evaluate ---
    pred_train = model.predict(X_train)
    pred_val   = model.predict(X_val)
    pred_test  = model.predict(X_test)

    logger.info("\n" + "=" * 60)
    logger.info("EVALUATION SUMMARY")
    logger.info("=" * 60)

    for name, y_true, y_pred in [
        ("train", y_train, pred_train),
        ("val",   y_val,   pred_val),
        ("test",  y_test,  pred_test),
    ]:
        ic   = ic_score(y_true, y_pred)
        qs   = quintile_spread(y_true, y_pred)
        rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
        logger.info(f"[{name:5s}]  IC={ic:+.4f}  Q5-Q1 spread={qs:+.5f}  RMSE={rmse:.5f}")

    logger.info("=" * 60)
    logger.info("IC: <0.02=noise | 0.02-0.05=weak | 0.05-0.10=useful | >0.10=check leakage")

    # Quintile breakdown on val (the one that matters)
    logger.info("\nVal quintile breakdown (predicted rank → actual return):")
    logger.info("\n" + quintile_table(y_val, pred_val).to_string())

    # --- Feature importance ---
    fi = pd.DataFrame({
        "feature":    FEATURE_COLS,
        "importance": model.feature_importances_,
    }).sort_values("importance", ascending=False).reset_index(drop=True)

    logger.info(f"\nTop 15 features by gain:\n{fi.head(15).to_string(index=False)}")
    fi.to_csv(FI_PATH, index=False)

    # --- Save ---
    model.save_model(MODEL_PATH)
    logger.info(f"\nSaved model → {MODEL_PATH}")
    logger.info(f"Saved feature importance → {FI_PATH}")


if __name__ == "__main__":
    run()