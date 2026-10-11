"""Keyword news search through Moomoo OpenD, then a local date filter.

get_search_news has no start or end argument. Each keyword returns the latest
matches (at most max_count). Publish times often look like 5/13, with no year.
That form is read as the latest such day on or before today, then kept only
when that date sits inside the requested window.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from core.history_client import HOST, PORT, OpenDSessionError

DEFAULT_KEYWORDS = (
    "semiconductor",
    "crude oil",
    "war",
    "bitcoin",
    "sector rotation",
)
MAX_COUNT_CAP = 200
REQUEST_GAP_S = 3.2
SUBTYPES = ("ALL", "NEWS", "NOTICE", "RATING")

_ABS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d-%b-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
)
_MONTH_DAY = re.compile(r"^(\d{1,2})[/-](\d{1,2})$")
_CN_DATE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$")


@dataclass
class NewsPull:
    saved: int
    fetched: int
    outside: int
    unparsed: int
    path: Path | None
    rows: list[dict]


def split_keywords(text: str, limit: int = 20) -> list[str]:
    found: list[str] = []
    for raw in text.replace(",", "\n").splitlines():
        item = " ".join(raw.split())
        if item and item not in found:
            found.append(item)
    if len(found) > limit:
        raise ValueError(f"Use at most {limit} keywords")
    return found


def classify_publish(raw, start: date, end: date) -> tuple[str, date | None]:
    """Return ('in', date), ('outside', date or None), or ('unparsed', None)."""
    text = "" if raw is None else str(raw).strip()
    if not text or text == "N/A":
        return "unparsed", None

    if text.isdigit() and len(text) in (10, 13):
        stamp = int(text)
        if len(text) == 13:
            stamp //= 1000
        try:
            found = datetime.fromtimestamp(stamp).date()
        except (OverflowError, OSError, ValueError):
            return "unparsed", None
        if start <= found <= end:
            return "in", found
        return "outside", found

    cn = _CN_DATE.fullmatch(text)
    if cn:
        try:
            found = date(int(cn.group(1)), int(cn.group(2)), int(cn.group(3)))
        except ValueError:
            return "unparsed", None
        if start <= found <= end:
            return "in", found
        return "outside", found

    for fmt in _ABS_FORMATS:
        try:
            found = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if start <= found <= end:
            return "in", found
        return "outside", found

    month_day = _MONTH_DAY.fullmatch(text)
    if month_day:
        found = _latest_month_day(int(month_day.group(1)), int(month_day.group(2)))
        if found is None:
            return "unparsed", None
        if start <= found <= end:
            return "in", found
        return "outside", found

    return "unparsed", None


def _latest_month_day(month: int, day: int, today: date | None = None) -> date | None:
    """Latest month/day on or before today. 12/20 in January is last year."""
    today = date.today() if today is None else today
    for year in (today.year, today.year - 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= today:
            return candidate
    return None


def pull_news(
    keywords: list[str],
    start: date,
    end: date,
    max_count: int,
    news_sub_type: str,
    out_path: Path,
    log: Callable[[str], None],
) -> NewsPull:
    """Search each keyword, keep rows inside start..end, write CSV. Returns counts."""
    if not keywords:
        raise ValueError("Enter at least one keyword")
    if start > end:
        raise ValueError("From is after To")
    if news_sub_type not in SUBTYPES:
        raise ValueError(f"News type must be one of {', '.join(SUBTYPES)}")
    if not isinstance(max_count, int) or not 1 <= max_count <= MAX_COUNT_CAP:
        raise ValueError(f"Max per keyword is 1–{MAX_COUNT_CAP}")

    try:
        import pandas as pd
        from moomoo import RET_OK, NewsSubType, OpenQuoteContext
    except Exception as exc:
        raise OpenDSessionError(f"Import failed: {exc}. pip install moomoo-api pandas") from exc

    subtype = getattr(NewsSubType, news_sub_type)
    try:
        quote_ctx = OpenQuoteContext(host=HOST, port=PORT)
    except Exception as exc:
        raise OpenDSessionError(f"{exc}") from exc

    fetched = 0
    outside = 0
    unparsed = 0
    kept: list[dict] = []
    try:
        log(f"Connected OpenQuoteContext {HOST}:{PORT}")
        for index, keyword in enumerate(keywords):
            if index:
                log(f"Waiting {REQUEST_GAP_S:.0f}s  ·  10 searches / 30s")
                time.sleep(REQUEST_GAP_S)
            log(f"API get_search_news  {keyword!r}  max={max_count}  type={news_sub_type}")
            ret, data = quote_ctx.get_search_news(keyword, max_count=max_count, news_sub_type=subtype)
            if ret != RET_OK:
                raise OpenDSessionError(f"API error for {keyword!r}: {data}")
            n = 0 if data is None else len(data)
            fetched += n
            log(f"API ok {keyword!r}: {n} items")
            if n == 0:
                continue
            for record in data.to_dict(orient="records"):
                kind, found = classify_publish(record.get("publish_time"), start, end)
                if kind == "unparsed":
                    unparsed += 1
                    continue
                if kind != "in" or found is None:
                    outside += 1
                    continue
                symbols = record.get("related_securities") or []
                if isinstance(symbols, list):
                    symbols = "|".join(str(item) for item in symbols)
                kept.append(
                    {
                        "keyword": keyword,
                        "date": found.isoformat(),
                        "publish_time": record.get("publish_time", ""),
                        "title": record.get("title", ""),
                        "news_sub_type": record.get("news_sub_type", ""),
                        "source": record.get("source", ""),
                        "view_count": record.get("view_count", ""),
                        "related_securities": symbols,
                        "url": record.get("url", ""),
                    }
                )

        kept.sort(key=lambda row: (row["date"], row["keyword"], str(row["title"])), reverse=True)
        frame = pd.DataFrame(kept)
        path: Path | None = None
        if kept:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(out_path, index=False)
            path = out_path
            log(f"Saved {len(kept)} items -> {out_path.name}")
        else:
            log("No items inside the date window")
        log(f"Fetched {fetched}  ·  in range {len(kept)}  ·  outside {outside}  ·  no date {unparsed}")
        return NewsPull(len(kept), fetched, outside, unparsed, path, kept)
    finally:
        quote_ctx.close()
        log("Quote context closed")
