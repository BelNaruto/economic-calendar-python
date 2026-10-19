"""pandas helpers: DataFrames and news-blackout masks for backtesting."""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable

import pandas as pd

from .client import Event


def to_dataframe(events: Iterable[Event]) -> pd.DataFrame:
    """One row per event, `datetime_utc` as a tz-aware pandas Timestamp."""
    df = pd.DataFrame([e.to_dict() for e in events])
    if df.empty:
        return df
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
    df["surprise"] = [e.surprise() for e in events] if isinstance(events, list) else None
    return df


def news_blackout_mask(index: pd.DatetimeIndex, events: Iterable[Event], *, before: int = 30, after: int = 30,
                       importance: tuple[str, ...] = ("high",), currencies: tuple[str, ...] | None = None) -> pd.Series:
    """True for timestamps within [event - before, event + after] minutes of a matching release.

    Typical use: skip trades around high-impact news in a backtest or a live strategy.

        mask = news_blackout_mask(prices.index, events, before=15, after=45, currencies=("USD",))
        signals[mask] = 0
    """
    idx = pd.DatetimeIndex(index)
    idx_utc = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    mask = pd.Series(False, index=index)
    b, a = timedelta(minutes=before), timedelta(minutes=after)
    for e in events:
        if not e.when or (importance and e.importance not in importance):
            continue
        if currencies and (e.currency or "").upper() not in currencies:
            continue
        start, end = pd.Timestamp(e.when - b), pd.Timestamp(e.when + a)
        mask |= (idx_utc >= start) & (idx_utc <= end)
    return mask
