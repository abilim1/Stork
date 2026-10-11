"""Read-only OpenD integration. Prices retain their source session and timestamp."""
from __future__ import annotations

import math
import socket
import threading
import time
from datetime import datetime, timedelta

from .market import NEW_YORK, change, session_at, timestamp, validate_bar

from .stocks import STOCKS, stock_symbol

SYMBOLS = [*STOCKS, "US.QQQ", "US.SMH", "US.SPY"]
SESSION_STATES = {"MORNING": "regular", "AFTERNOON": "regular", "PRE_MARKET_BEGIN": "premarket", "AFTER_HOURS_BEGIN": "afterhours", "OVERNIGHT": "overnight"}


def positive(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError):
        return None


def normalize_quote(row, market_state, received=None):
    received = time.time() if received is None else received
    session = SESSION_STATES.get(str(market_state), session_at(received))
    prefix = {"premarket": "pre", "afterhours": "after", "overnight": "overnight"}.get(session)
    price = positive(row.get(f"{prefix}_price")) if prefix else positive(row.get("last_price"))
    # Never present the regular close as a live extended-hours trade.
    if prefix and price is None:
        return None
    price = price or positive(row.get("last_price"))
    if price is None:
        return None
    observed = row.get("update_time") or row.get("data_date", "") + " " + row.get("data_time", "")
    try:
        quote_time = timestamp(observed.strip())
    except (ValueError, TypeError):
        quote_time = None
    reference = positive(row.get("prev_close_price"))
    regular_price = positive(row.get("last_price"))
    # Before the next regular open, prev_close_price can still refer to TWO sessions
    # ago. The last regular price is the close relevant to all extended sessions.
    if session in ("premarket", "afterhours", "overnight"):
        reference = regular_price
    return {"symbol": row["code"], "price": price, "reference": reference, "change_pct": change(price, reference), "regular_price": regular_price, "session": session, "time": quote_time, "received": received, "source": "Moomoo quote", "volume": positive(row.get(f"{prefix}_volume" if prefix else "volume")), "time_note": "Provider quote update time; session trade time may differ"}


class OpenDFeed:
    def __init__(self, store, host="127.0.0.1", port=11111):
        self.store, self.host, self.port = store, host, port
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.context = None
        self.status = {"state": "disconnected", "message": "Connect OpenD to receive current prices", "host": host, "port": port, "market_state": "UNKNOWN", "last_received": None, "subscriptions": [], "errors": []}
        self.quotes = {}
        self.alert_side = {}

    def snapshot(self, symbol="US.MU"):
        with self.lock:
            result = {**self.status, "quotes": dict(self.quotes)}
        result["symbol"] = symbol
        result["market_state"] = result.get("market_states", {}).get(symbol, result["market_state"])
        result["age_seconds"] = time.time() - result["last_received"] if result["last_received"] else None
        return result

    def connect(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.snapshot()
            self.stop_event.clear()
            self.status.update(state="connecting", message="Connecting to OpenD…", errors=[], subscriptions=[])
            self.thread = threading.Thread(target=self._maintain_connection, name="stork-opend", daemon=True)
            self.thread.start()
        return self.snapshot()

    def disconnect(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        with self.lock:
            self.status.update(state="disconnected", message="OpenD feed stopped")

    def _error(self, message):
        with self.lock:
            self.status["errors"] = (self.status["errors"] + [str(message)[:500]])[-5:]

    def _maintain_connection(self):
        while not self.stop_event.is_set():
            self._run()
            if self.stop_event.wait(30):
                return
            with self.lock:
                self.status.update(state="connecting", message="Reconnecting to OpenD…", subscriptions=[])

    def _run(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=3):
                pass
            import moomoo as mm
            owner = self

            class Quotes(mm.StockQuoteHandlerBase):
                def on_recv_rsp(self, response):
                    ret, data = super().on_recv_rsp(response)
                    if ret == mm.RET_OK:
                        owner._quotes(data.to_dict("records"))
                    else:
                        owner._error(data)
                    return ret, data

            class Candles(mm.CurKlineHandlerBase):
                def on_recv_rsp(self, response):
                    ret, data = super().on_recv_rsp(response)
                    if ret == mm.RET_OK:
                        owner._bars(data.to_dict("records"), 1)
                    else:
                        owner._error(data)
                    return ret, data

            class Ticks(mm.TickerHandlerBase):
                def on_recv_rsp(self, response):
                    ret, data = super().on_recv_rsp(response)
                    if ret == mm.RET_OK:
                        for row in data.to_dict("records"):
                            if row.get("code") in STOCKS:
                                owner._tick(row)
                    else:
                        owner._error(data)
                    return ret, data

            q = mm.OpenQuoteContext(host=self.host, port=self.port)
            self.context = q
            q.set_handler(Quotes())
            q.set_handler(Candles())
            q.set_handler(Ticks())
            self._poll(q, mm)
            requests = [(SYMBOLS, [mm.SubType.QUOTE]), (list(STOCKS), [mm.SubType.K_1M, mm.SubType.TICKER])]
            for codes, types in requests:
                ret, detail = q.subscribe(codes, types, subscribe_push=True, session=mm.Session.ALL)
                if ret != mm.RET_OK:
                    self._error(f"Subscription: {detail}")
                else:
                    with self.lock:
                        self.status["subscriptions"] = self.status["subscriptions"] + [str(t) for t in types]
            today = datetime.now(NEW_YORK).date()
            # Backfill recent data once; large historical exports remain local.
            for symbol in STOCKS:
                for minutes, start in [(15, today - timedelta(days=22)), (1, today - timedelta(days=3))]:
                    key = None
                    for _ in range(30):
                        if self.stop_event.is_set():
                            break
                        ret, data, key = q.request_history_kline(symbol, start=start.isoformat(), end=today.isoformat(), ktype=mm.KLType.K_15M if minutes == 15 else mm.KLType.K_1M, autype=mm.AuType.QFQ, max_count=1000, page_req_key=key, extended_time=True)
                        if ret != mm.RET_OK:
                            self._error(f"{symbol} history: {data}")
                            break
                        self._bars(data.to_dict("records"), minutes)
                        if key is None:
                            break
            with self.lock:
                self.status.update(state="connected", message="OpenD connected • streaming with 30-second quote refresh")
            while not self.stop_event.wait(30):
                self._poll(q, mm)
        except Exception as exc:
            with self.lock:
                self.status.update(state="error", message=str(exc)[:500])
            self.store.event("error", "OpenD connection needs attention", str(exc)[:500])
        finally:
            if self.context:
                self.context.close()
                self.context = None

    def _poll(self, q, mm):
        ret, states = q.get_market_state(SYMBOLS)
        if ret != mm.RET_OK:
            raise RuntimeError(f"Market status: {states}")
        states = {r["code"]: r["market_state"] for r in states.to_dict("records")}
        with self.lock:
            self.status["market_states"] = states
            self.status["market_state"] = states.get("US.MU", "UNKNOWN")
        ret, data = q.get_market_snapshot(SYMBOLS)
        if ret != mm.RET_OK:
            raise RuntimeError(f"Quote refresh: {data}")
        self._quotes(data.to_dict("records"))
        with self.lock:
            self.status["last_received"] = time.time()

    def _quotes(self, rows):
        for row in rows:
            quote = normalize_quote(row, self.status.get("market_states", {}).get(row["code"], self.status["market_state"]))
            if quote:
                with self.lock:
                    old = self.quotes.get(quote["symbol"], {})
                    if (old.get("time") or 0) > (quote["time"] or 0):
                        if old.get("session") == quote["session"]:
                            old.update(reference=quote["reference"], regular_price=quote["regular_price"], volume=quote["volume"], change_pct=change(old["price"], quote["reference"]))
                        continue
                    self.quotes[quote["symbol"]] = quote
                    self.status["last_received"] = time.time()
                if quote["symbol"] in STOCKS and quote["time"] and -5 <= time.time() - quote["time"] < 120:
                    self._check_alerts(quote["price"], quote["symbol"])

    def _bars(self, rows, interval):
        grouped = {}
        for row in rows:
            try:
                symbol = stock_symbol(row.get("code", "US.MU"))
                grouped.setdefault(symbol, []).append(validate_bar(row, interval, "moomoo"))
            except (ValueError, KeyError) as exc:
                self._error(f"Candle rejected: {exc}")
        for symbol, bars in grouped.items():
            self.store.upsert_bars(bars, symbol)

    def _tick(self, row):
        try:
            symbol = stock_symbol(row.get("code", "US.MU"))
            ts = timestamp(row["time"])
            price, volume = float(row["price"]), float(row["volume"])
            if not math.isfinite(price) or not math.isfinite(volume) or price <= 0 or volume < 0:
                return
            self.store.tick({"sequence": row["sequence"], "time": ts, "price": price, "volume": volume}, symbol)
            # Cached/replayed ticks must not move the live quote backwards.
            if -5 <= time.time() - ts < 90:
                with self.lock:
                    old = self.quotes.get(symbol, {})
                    if ts < (old.get("time") or 0):
                        return
                    session = session_at(ts)
                    # Wait for the session's quote reference before calculating its change.
                    reference = old.get("reference") if old.get("session") == session else None
                    self.quotes[symbol] = {**old, "symbol": symbol, "price": price, "time": ts, "received": time.time(), "session": session, "reference": reference, "source": "Moomoo trade", "change_pct": change(price, reference), "time_note": "Trade timestamp"}
                    self.status["last_received"] = time.time()
                self._check_alerts(price, symbol)
        except (ValueError, KeyError, TypeError) as exc:
            self._error(f"Tick rejected: {exc}")

    def _check_alerts(self, price, symbol="US.MU"):
        settings = self.store.settings(symbol)
        for direction in ("above", "below"):
            threshold = settings[f"alert_{direction}"]
            hit = threshold is not None and (price >= threshold if direction == "above" else price <= threshold)
            key = (symbol, direction, threshold)
            if hit and not self.alert_side.get(key):
                self.store.event("price", f"{STOCKS[symbol]['ticker']} crossed {direction} ${threshold:,.2f}", f"Latest observed price ${price:,.2f}")
            self.alert_side[key] = hit
