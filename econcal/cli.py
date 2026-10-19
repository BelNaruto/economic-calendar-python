"""econcal command line.

  econcal fetch    --countries us,ez --importance high --from 2026-10-12 --to 2026-10-19 --format xlsx --out week.xlsx
  econcal next     --count 10 --importance high
  econcal blackout --currencies USD,EUR --before 30 --after 30 --out news_windows.csv
  (add --sample tests/sample-events.json to any command to try it offline)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import ACTOR_URL, ActorError, Event, flag, load_events

ALIASES = {"us": "united states", "usa": "united states", "ez": "euro zone", "eu": "euro zone", "eurozone": "euro zone",
           "uk": "united kingdom", "gb": "united kingdom", "jp": "japan", "de": "germany", "fr": "france",
           "ca": "canada", "au": "australia", "nz": "new zealand", "ch": "switzerland", "cn": "china",
           "uae": "united arab emirates"}


def _countries(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [ALIASES.get(c.strip().lower(), c.strip().lower()) for c in value.split(",") if c.strip()]


def _date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _load(args) -> list[Event]:
    kw = {"countries": _countries(args.countries)}
    if args.importance and args.importance != "all":
        kw["importance"] = args.importance
    if getattr(args, "from_date", None):
        kw["from_date"] = _date(args.from_date)
    if getattr(args, "to_date", None):
        kw["to_date"] = _date(args.to_date)
    return load_events(sample_file=args.sample, days_back=0, days_ahead=getattr(args, "days", 7), **kw)


def _local(e: Event, tz: ZoneInfo) -> str:
    return e.when.astimezone(tz).strftime("%Y-%m-%d %H:%M") if e.when else f"{e.day} all day"


def print_table(events: list[Event], tz: ZoneInfo, title: str) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:  # plain fallback
        for e in events:
            print(f"{_local(e, tz):17} {e.currency or '':4} {e.importance or 'holiday':7} {e.title:55} "
                  f"A {e.actual or '-':>8}  F {e.forecast or '-':>8}  P {e.previous or '-':>8}")
        return
    t = Table(title=title, title_style="bold", header_style="bold cyan", show_lines=False, expand=False)
    for col, kw in [("Time (" + tz.key + ")", {}), ("", {}), ("Cur", {}), ("Impact", {}), ("Event", {"max_width": 46}),
                    ("Actual", {"justify": "right"}), ("Forecast", {"justify": "right"}), ("Previous", {"justify": "right"})]:
        t.add_column(col, **kw)
    style = {"high": "bold red", "medium": "yellow", "low": "dim"}
    for e in events:
        s = e.surprise()
        actual = f"[{'green' if s == 'beat' else 'red' if s == 'miss' else 'white'}]{e.actual}[/]" if e.actual else ""
        t.add_row(_local(e, tz), flag(e.country), e.currency or "", f"[{style.get(e.importance or '', 'green')}]"
                  f"{(e.importance or 'holiday').upper()}[/]", e.title, actual, e.forecast or "", e.previous or "")
    Console().print(t)
    Console().print(f"[dim]{len(events)} events · data: Investing.com via {ACTOR_URL}[/]")


def write(events: list[Event], fmt: str, out: str | None, tz: ZoneInfo) -> None:
    rows = [e.to_dict() | {"local_time": _local(e, tz), "surprise": e.surprise()} for e in events]
    if fmt == "json":
        text = json.dumps(rows, indent=2, ensure_ascii=False)
        Path(out).write_text(text, encoding="utf-8") if out else print(text)
    elif fmt == "csv":
        fh = open(out, "w", newline="", encoding="utf-8") if out else sys.stdout
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["event"])
        w.writeheader(); w.writerows(rows)
        if out:
            fh.close()
    elif fmt == "xlsx":
        import pandas as pd
        out = out or "economic-calendar.xlsx"
        df = pd.DataFrame(rows)
        with pd.ExcelWriter(out, engine="openpyxl") as xw:
            df.to_excel(xw, index=False, sheet_name="Calendar")
            ws = xw.sheets["Calendar"]
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(48, max(10, max(len(str(c.value or "")) for c in col) + 2))
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
    if out:
        print(f"wrote {len(rows)} events to {out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="econcal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--countries", help="comma list: us,ez,uk,jp or full names (97 supported)")
        sp.add_argument("--importance", default="all", choices=["high", "medium", "low", "all"])
        sp.add_argument("--tz", default="UTC", help="display time zone, e.g. America/New_York")
        sp.add_argument("--sample", help="offline: use a JSON export instead of calling the Actor")

    f = sub.add_parser("fetch", help="download events (table, csv, json, xlsx)")
    common(f)
    f.add_argument("--from", dest="from_date"); f.add_argument("--to", dest="to_date")
    f.add_argument("--days", type=int, default=7, help="if --from/--to omitted: today + N days")
    f.add_argument("--format", default="table", choices=["table", "csv", "json", "xlsx"])
    f.add_argument("--out")

    n = sub.add_parser("next", help="show the next N upcoming events")
    common(n)
    n.add_argument("--count", type=int, default=10)
    n.add_argument("--days", type=int, default=14)
    n.add_argument("--now", help="pretend current time (ISO UTC)")

    b = sub.add_parser("blackout", help="news blackout windows CSV for backtests / trading bots / EAs")
    common(b)
    b.add_argument("--from", dest="from_date"); b.add_argument("--to", dest="to_date")
    b.add_argument("--days", type=int, default=14)
    b.add_argument("--before", type=int, default=30); b.add_argument("--after", type=int, default=30)
    b.add_argument("--currencies", help="e.g. USD,EUR")
    b.add_argument("--out")

    args = p.parse_args(argv)
    tz = ZoneInfo(args.tz)
    if args.cmd == "blackout" and args.importance == "all":
        args.importance = "high"
    try:
        events = _load(args)
    except ActorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "fetch":
        if args.format == "table":
            print_table(events, tz, "Economic calendar")
        else:
            write(events, args.format, args.out, tz)
    elif args.cmd == "next":
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
        upcoming = [e for e in events if e.when and e.when > now][: args.count]
        print_table(upcoming, tz, f"Next {len(upcoming)} events")
    else:
        cur = {c.strip().upper() for c in args.currencies.split(",")} if args.currencies else None
        fh = open(args.out, "w", newline="") if args.out else sys.stdout
        w = csv.writer(fh)
        w.writerow(["start_utc", "end_utc", "currency", "importance", "event"])
        count = 0
        for e in events:
            if not e.when or (cur and (e.currency or "").upper() not in cur):
                continue
            w.writerow([(e.when - timedelta(minutes=args.before)).strftime("%Y-%m-%d %H:%M"),
                        (e.when + timedelta(minutes=args.after)).strftime("%Y-%m-%d %H:%M"),
                        e.currency, e.importance, e.title])
            count += 1
        if args.out:
            fh.close()
            print(f"wrote {count} blackout windows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
