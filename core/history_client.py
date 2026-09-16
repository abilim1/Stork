"""Moomoo history K-line pull. One symbol, paginated."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

HOST = "127.0.0.1"
PORT = 11111
MAX_COUNT = 1000
DROP_COLS = ("pe_ratio", "turnover_rate")

LOGIN_HINTS = (
    "login",
    "log in",
    "questionnaire",
    "not ready",
    "connection refused",
    "connect",
    "exited",
    "verify",
    "permission",
    "auth",
    "unlock",
    "opend",
)

LOGIN_PROMPT = (
    "Pull failed and will not retry.\n\n"
    "Check:\n"
    "1. OpenD Client is still open\n"
    "2. Login finished (SMS / questionnaire if asked)\n"
    "3. Log shows Required data is ready\n"
    "4. Nothing else is using port 11111"
)


class OpenDSessionError(RuntimeError):
    """API or gateway failed. Caller must not retry this pull."""


def looks_like_login_issue(message: str) -> bool:
    text = message.lower()
    return any(hint in text for hint in LOGIN_HINTS)


def pull_history(
    code: str,
    start: str,
    end: str,
    ktype,
    extended: bool,
    out_path: Path,
    log: Callable[[str], None],
) -> int:
    """
    Pull bars and write CSV. Returns bar count.
    Raises OpenDSessionError on API / import failure. Does not retry.
    """
    try:
        import pandas as pd
        from moomoo import RET_OK, AuType, OpenQuoteContext
    except Exception as exc:
        raise OpenDSessionError(f"Import failed: {exc}. pip install moomoo-api pandas") from exc

    try:
        quote_ctx = OpenQuoteContext(host=HOST, port=PORT)
    except Exception as exc:
        raise OpenDSessionError(f"{exc}") from exc

    try:
        log(f"Connected OpenQuoteContext {HOST}:{PORT}")
        ret, used_remain = quote_ctx.get_history_kl_quota(get_detail=False)
        if ret == RET_OK:
            log(f"API quota  used={used_remain[0]}  remain={used_remain[1]}")
        else:
            log(f"API get_history_kl_quota: {used_remain}")

        pages = []
        page_req_key = None
        page = 0
        while True:
            page += 1
            log(f"API request_history_kline page={page} start={start} end={end}")
            ret, data, page_req_key = quote_ctx.request_history_kline(
                code,
                start=start,
                end=end,
                ktype=ktype,
                autype=AuType.QFQ,
                max_count=MAX_COUNT,
                page_req_key=page_req_key,
                extended_time=extended,
            )
            if ret != RET_OK:
                raise OpenDSessionError(f"API error page {page}: {data}")

            n = 0 if data is None else len(data)
            log(f"API ok page {page}: {n} bars")
            if n == 0:
                break
            pages.append(data)
            if page_req_key is None:
                log("API page_req_key empty — last page")
                break

        if not pages:
            log("No bars returned")
            return 0

        out = pd.concat(pages, ignore_index=True)
        if "time_key" in out.columns:
            out = out.drop_duplicates(subset=["time_key"], keep="last")
            out = out.sort_values("time_key").reset_index(drop=True)
        drop_cols = [c for c in DROP_COLS if c in out.columns]
        if drop_cols:
            out = out.drop(columns=drop_cols)
            log(f"Dropped columns: {', '.join(drop_cols)}")
        out.to_csv(out_path, index=False)
        first = out["time_key"].iloc[0] if "time_key" in out.columns else "?"
        last = out["time_key"].iloc[-1] if "time_key" in out.columns else "?"
        log(f"Saved {len(out)} bars -> {out_path.name}")
        log(f"Range {first} -> {last}")

        ret, used_remain = quote_ctx.get_history_kl_quota(get_detail=False)
        if ret == RET_OK:
            log(f"API quota after  used={used_remain[0]}  remain={used_remain[1]}")
        return len(out)
    finally:
        quote_ctx.close()
        log("Quote context closed")
