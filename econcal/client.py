"""Tiny client for the "Economic Calendar Data (Investing.com)" Apify Actor.

Actor: https://apify.com/pintostudio/economic-calendar-data-investing-com

The Actor scrapes the Investing.com economic calendar (CPI, NFP, FOMC, GDP, PMIs,
central-bank decisions, speeches, holidays...) and returns one JSON item per event:

    {"id": 557980, "date": "14/10/2026", "time": "12:30", "zone": "united states",
     "currency": "USD", "importance": "high", "event": "CPI (YoY) (Sep)",
     "actual": "3.7%", "forecast": "3.6%", "previous": "3.4%"}

Times are returned in GMT/UTC (the Actor's default time zone).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests

ACTOR_ID = "pintostudio~economic-calendar-data-investing-com"
ACTOR_URL = "https://apify.com/pintostudio/economic-calendar-data-investing-com"
API_URL = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"

IMPORTANCE_RANK = {"low": 1, "medium": 2, "high": 3}


class ActorError(RuntimeError):
    """Raised when the Apify Actor run fails or returns an unexpected payload."""


@dataclass(frozen=True)
class Event:
    id: int | None
    when: datetime | None  # timezone-aware UTC datetime; None for "All Day" / tentative events
    day: date
    all_day: bool
    country: str
    currency: str | None
    importance: str | None  # "high" | "medium" | "low" | None (holidays)
    title: str
    actual: str | None
    forecast: str | None
    previous: str | None

    @property
    def is_holiday(self) -> bool:
        return self.importance is None and self.all_day

    @property
    def stars(self) -> str:
        return "★" * IMPORTANCE_RANK.get(self.importance or "", 0)

    def surprise(self) -> str | None:
        """'beat' / 'miss' / 'inline' comparing actual vs forecast (numbers only)."""
        a, f = _to_number(self.actual), _to_number(self.forecast)
        if a is None or f is None:
            return None
        if abs(a - f) < 1e-9:
            return "inline"
        return "beat" if a > f else "miss"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "datetime_utc": self.when.isoformat() if self.when else None,
            "date": self.day.isoformat(),
            "all_day": self.all_day,
            "country": self.country,
            "currency": self.currency,
            "importance": self.importance,
            "event": self.title,
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
        }


def _to_number(value: str | None) -> float | None:
    if not value:
        return None
    v = value.strip().replace(",", "").replace("%", "")
    mult = 1.0
    if v and v[-1] in "KMBT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[v[-1]]
        v = v[:-1]
    try:
        return float(v) * mult
    except ValueError:
        return None


def parse_event(raw: dict) -> Event:
    day = datetime.strptime(raw["date"], "%d/%m/%Y").date()
    t = (raw.get("time") or "").strip()
    when = None
    all_day = True
    if len(t) == 5 and t[2] == ":":
        hh, mm = int(t[:2]), int(t[3:])
        when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc)
        all_day = False
    return Event(
        id=raw.get("id"),
        when=when,
        day=day,
        all_day=all_day,
        country=(raw.get("zone") or "").strip(),
        currency=raw.get("currency"),
        importance=raw.get("importance"),
        title=(raw.get("event") or "").strip(),
        actual=raw.get("actual"),
        forecast=raw.get("forecast"),
        previous=raw.get("previous"),
    )


def parse_events(items: Iterable[dict]) -> list[Event]:
    seen: set[tuple] = set()
    out: list[Event] = []
    for raw in items:
        if raw.get("data_type") == "error" or not raw.get("date"):
            continue
        ev = parse_event(raw)
        key = (ev.id, ev.day, ev.title)
        if key in seen:  # the source occasionally repeats holiday rows
            continue
        seen.add(key)
        out.append(ev)
    out.sort(key=lambda e: (e.day, e.when or datetime.combine(e.day, datetime.min.time(), timezone.utc)))
    return out


def filter_events(events: list[Event], *, countries: list[str] | None = None,
                  importance: str | None = None) -> list[Event]:
    """Apply the same filters the Actor applies (used for offline/sample data)."""
    if countries:
        wanted = {c.lower() for c in countries}
        events = [e for e in events if e.country.lower() in wanted]
    if importance:
        events = [e for e in events if e.importance == importance]
    return events


def fetch_raw(
    token: str,
    *,
    countries: list[str] | None = None,
    importance: str | None = None,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    categories: list[str] | None = None,
    timeout: int = 300,
) -> list[dict]:
    """Run the Actor synchronously and return its dataset items.

    Pricing is pay-per-result on Apify, so narrow filters (importance="high",
    a few countries) keep runs cheap.
    """
    payload: dict = {"timeFilter": "time_only"}
    if countries:
        payload["countries"] = [c.lower() for c in countries]
    if importance:
        payload["importances"] = importance
    if categories:
        payload["categories"] = categories
    if from_date:
        payload["fromDate"] = str(from_date)
    if to_date:
        payload["toDate"] = str(to_date)

    resp = requests.post(
        API_URL,
        params={"token": token, "clean": "true", "timeout": timeout},
        json=payload,
        timeout=timeout + 30,
    )
    if resp.status_code >= 400:
        raise ActorError(f"Actor run failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    if not isinstance(data, list):
        raise ActorError(f"Unexpected response: {str(data)[:300]}")
    return data


def load_events(
    *,
    token: str | None = None,
    sample_file: str | Path | None = None,
    days_back: int = 1,
    days_ahead: int = 14,
    **filters,
) -> list[Event]:
    """Fetch events from the Actor, or from a local JSON export (demo/offline mode)."""
    if sample_file:
        items = json.loads(Path(sample_file).read_text(encoding="utf-8"))
        return filter_events(parse_events(items), countries=filters.get("countries"),
                             importance=filters.get("importance"))
    token = token or os.environ.get("APIFY_TOKEN")
    if not token:
        raise ActorError(
            "Missing APIFY_TOKEN. Get a free token at https://console.apify.com/settings/integrations "
            f"and make sure you can run {ACTOR_URL}"
        )
    today = datetime.now(timezone.utc).date()
    filters.setdefault("from_date", today - timedelta(days=days_back))
    filters.setdefault("to_date", today + timedelta(days=days_ahead))
    return parse_events(fetch_raw(token, **filters))


COUNTRY_FLAGS = {
    "united states": "🇺🇸", "euro zone": "🇪🇺", "united kingdom": "🇬🇧", "japan": "🇯🇵",
    "germany": "🇩🇪", "france": "🇫🇷", "italy": "🇮🇹", "spain": "🇪🇸", "canada": "🇨🇦",
    "australia": "🇦🇺", "new zealand": "🇳🇿", "switzerland": "🇨🇭", "china": "🇨🇳",
    "india": "🇮🇳", "brazil": "🇧🇷", "mexico": "🇲🇽", "south korea": "🇰🇷", "portugal": "🇵🇹",
    "united arab emirates": "🇦🇪", "south africa": "🇿🇦", "türkiye": "🇹🇷", "turkey": "🇹🇷",
}


def flag(country: str) -> str:
    return COUNTRY_FLAGS.get(country.lower(), "🌐")
