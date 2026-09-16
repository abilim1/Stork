from datetime import date
import pandas as pd
from moomoo import RET_OK, AuType, KLType, OpenQuoteContext

quote_ctx = OpenQuoteContext(host="127.0.0.1", port=11111)

pages = []
page_req_key = None
while True:
    ret, data, page_req_key = quote_ctx.request_history_kline(
        "US.MU",
        start="2026-01-01",
        end=date.today().isoformat(),
        ktype=KLType.K_15M,
        autype=AuType.QFQ,
        max_count=1000,
        page_req_key=page_req_key,
        extended_time=True,
    )
    if ret != RET_OK:
        raise RuntimeError(data)
    if data is None or len(data) == 0:
        break
    pages.append(data)
    if page_req_key is None:
        break

df = pd.concat(pages, ignore_index=True)
df = df.drop_duplicates(subset=["time_key"]).sort_values("time_key")
ts = pd.to_datetime(df["time_key"], errors="coerce")
df.insert(0, "Date", ts.dt.strftime("%Y-%m-%d"))
df.insert(1, "Time", ts.dt.strftime("%H:%M:%S"))
df = df.drop(columns=["time_key", "pe_ratio", "turnover_rate"], errors="ignore")
df.to_csv("MU15m2026.csv", index=False)
quote_ctx.close()
