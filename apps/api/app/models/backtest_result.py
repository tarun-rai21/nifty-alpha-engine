import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[Optional[float]] = mapped_column(Float)
    horizon: Mapped[Optional[str]] = mapped_column(String(10))
    bucket_name: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )