"""Validated market data and session-aware aggregation. Provider bars are end-labelled."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def number(value, name="value", minimum=None):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} is outside the allowed range")
    return result


def timestamp(value):
    if isinstance(value, (int, float)):
        return number(value, "Timestamp")
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NEW_YORK)
    return parsed.timestamp()


def session_at(ts, bar_end=False):
    # A candle ending at 09:30 belongs to premarket; a trade at 09:30 is regular.
    dt = datetime.fromtimestamp(ts - (1 if bar_end else 0), NEW_YORK)
    minute = dt.hour * 60 + dt.minute
    if 240 <= minute < 570:
        return "premarket"
    if 570 <= minute < 960:
        return "regular"
    if 960 <= minute < 1200:
        return "afterhours"
    return "overnight"


def validate_bar(row, interval=15, source="csv"):
    ts = timestamp(row.get("time_key") or f"{row['Date']}T{row['Time']}")
    result = {"time": ts, "interval": interval, "source": source}
    for key in ("open", "high", "low", "close", "volume"):
        result[key] = number(row[key], key, minimum=0)
    if min(result[k] for k in ("open", "high", "low", "close")) <= 0:
        raise ValueError("Prices must be positive")
    if result["low"] > min(result["open"], result["close"]) or result["high"] < max(result["open"], result["close"]) or result["low"] > result["high"]:
        raise ValueError("Invalid OHLC relationship")
    return result


def change(price, reference):
    return (price / reference - 1) * 100 if price is not None and reference else None


def local_day(ts):
    return datetime.fromtimestamp(ts, NEW_YORK).date().isoformat()


def aggregate(rows, interval="15m", session="all"):
    if interval not in INTERVALS or session not in ("all", "regular", "premarket", "afterhours", "overnight"):
        raise ValueError("Unsupported chart interval or session")
    target = INTERVALS[interval]
    candidates = [r for r in rows if r["interval"] <= target and target % r["interval"] == 0 and (session == "all" or session_at(r["time"], True) == session)]
    # Keep provider coarse bars authoritative; never sum overlapping 1m and 15m data.
    buckets = defaultdict(list)
    origins = {"premarket": 240, "regular": 570, "afterhours": 960, "overnight": 0}
    for row in candidates:
        dt = datetime.fromtimestamp(row["time"] - 1, NEW_YORK)
        segment = session_at(row["time"], True)
        if target == 1440:
            bucket = (dt.date().isoformat(), "day", 0)
        else:
            minute = dt.hour * 60 + dt.minute
            bucket = (dt.date().isoformat(), segment, (minute - origins[segment]) // target)
        buckets[bucket].append(row)
    result = []
    for group in buckets.values():
        # Fine candles extend coverage only where no provider coarse candle exists.
        selected = []
        covered = []
        for row in sorted(group, key=lambda r: (-r["interval"], r["time"])):
            start, end = row["time"] - row["interval"] * 60, row["time"]
            if any(start < b and end > a for a, b in covered):
                continue
            selected.append(row)
            covered.append((start, end))
        selected.sort(key=lambda r: r["time"])
        first, last = selected[0], selected[-1]
        result.append({"time": last["time"], "open": first["open"], "high": max(r["high"] for r in selected), "low": min(r["low"] for r in selected), "close": last["close"], "volume": sum(r["volume"] for r in selected), "session": session_at(last["time"], True), "date": local_day(last["time"] - 1)})
    return sorted(result, key=lambda r: r["time"])


def performance(rows):
    regular = aggregate(rows, "1d", "regular")
    friday_returns = []
    for index, day in enumerate(regular):
        dt = datetime.fromtimestamp(day["time"], NEW_YORK)
        if index and dt.weekday() == 4 and dt.hour == 16 and dt.minute == 0:
            previous_day = regular[index - 1]
            gap = (dt.date() - datetime.fromisoformat(previous_day["date"]).date()).days
            if gap == 1:
                friday_returns.append({"date": day["date"], "return_pct": change(day["close"], previous_day["close"])})
    latest = rows[-1] if rows else None
    last_regular = regular[-1] if regular else None
    prior = regular[-2] if len(regular) > 1 else None
    weekly = defaultdict(list)
    for day in regular:
        dt = datetime.fromisoformat(day["date"])
        monday = (dt - timedelta(days=dt.weekday())).date().isoformat()
        weekly[monday].append(day)
    weeks = []
    previous = None
    for monday, days in sorted(weekly.items()):
        close = days[-1]["close"]
        end_time = datetime.fromtimestamp(days[-1]["time"], NEW_YORK)
        weeks.append({"week": monday, "end": days[-1]["date"], "close": close, "return_pct": change(close, previous), "session_count": len(days), "volume": sum(d["volume"] for d in days), "partial": len(days) < 5 or end_time.weekday() != 4 or end_time.hour < 16})
        previous = close
    friday = {"sample_size": len(friday_returns), "negative_count": sum(r["return_pct"] < 0 for r in friday_returns), "mean_return_pct": sum(r["return_pct"] for r in friday_returns) / len(friday_returns) if friday_returns else None, "start": friday_returns[0]["date"] if friday_returns else None, "end": friday_returns[-1]["date"] if friday_returns else None, "definition": "Observed Friday 16:00 close versus preceding Thursday close; missing pairs and early closes excluded. Descriptive sample, not a forecast."}
    return {"daily": regular[-30:], "weekly": weeks[-12:], "friday": friday, "latest_bar": latest, "regular_close": last_regular["close"] if last_regular else None, "prior_regular_close": prior["close"] if prior else None}
