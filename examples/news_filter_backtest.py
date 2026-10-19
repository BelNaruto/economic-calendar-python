"""Skip trades around high-impact news — minimal example.

    python examples/news_filter_backtest.py            # uses bundled sample data
    APIFY_TOKEN=... python examples/news_filter_backtest.py --live
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from econcal import load_events, news_blackout_mask  # noqa: E402

live = "--live" in sys.argv
events = load_events(
    sample_file=None if live else Path(__file__).resolve().parents[1] / "tests/sample-events.json",
    countries=["united states", "euro zone"], importance="high",
)

# Fake 5-minute EURUSD-like price series just for the demo (replace with your own data)
idx = pd.date_range("2026-10-12", "2026-10-17", freq="5min", tz="UTC")
rng = np.random.default_rng(7)
prices = pd.Series(1.08 + rng.normal(0, 0.0004, len(idx)).cumsum(), index=idx)

signal = np.sign(prices.diff(12)).fillna(0)          # toy momentum strategy
blackout = news_blackout_mask(idx, events, before=15, after=45, currencies=("USD", "EUR"))
filtered = signal.where(~blackout, 0)

print(f"{len(events)} high-impact USD/EUR events loaded")
print(f"bars in news blackout: {blackout.sum()} of {len(idx)} ({blackout.mean():.1%})")
print("first blackout windows:")
print(blackout[blackout].index.to_series().diff().ne(pd.Timedelta('5min')).cumsum().pipe(
    lambda g: blackout[blackout].index.to_series().groupby(g).agg(['min', 'max'])).head())
