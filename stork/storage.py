"""Shared storage operations with SQLite as the default backend. Existing CSV exports are read, never rewritten."""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
import time
from pathlib import Path

from .market import number, validate_bar
from .stocks import stock_symbol, stock_key

DEFAULT_SETTINGS = {
    "analysis_enabled": False,
    "premarket_interval": 30, "regular_interval": 15, "afterhours_interval": 30,
    "overnight_interval": 60, "overnight_enabled": False,
    "notify_reports": False, "alert_above": None, "alert_below": None,
    "timezone": "America/New_York", "chart_interval": "15m", "chart_style": "line",
    "weights": {"company": 25, "semiconductors": 20, "price_volume": 20, "macro": 15, "rotation": 10, "oil_geopolitics": 7, "crypto_sentiment": 3},
}


class Store:
    report_symbol_sql = "COALESCE(json_extract(body, '$.symbol'), 'US.MU')"
    position_symbol_sql = "COALESCE(json_extract(history, '$[0].symbol'), 'US.MU')"
    tick_symbol_sql = "(CASE WHEN instr(sequence, ':') = 0 THEN 'US.MU' ELSE substr(sequence, 1, instr(sequence, ':')-1) END)"

    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS bars(symbol TEXT, time INTEGER, interval INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, source TEXT, PRIMARY KEY(symbol,time,interval));
        CREATE TABLE IF NOT EXISTS documents(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, created REAL, status TEXT, body TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, created REAL, quantity REAL, entry REAL, intent TEXT, reason TEXT, state TEXT DEFAULT 'open', exit REAL, history TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, created REAL, kind TEXT, title TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS ticks(sequence TEXT PRIMARY KEY, time REAL, price REAL, volume REAL);
        """)
        self.db.commit()

    def _execute(self, query, parameters=()):
        return self.db.execute(query, parameters)

    def _executemany(self, query, values):
        self.db.executemany(query, values)

    def _transaction(self):
        return self.db

    def _write(self):
        return self._transaction()

    def _insert_id(self, query, parameters):
        return self._execute(query, parameters).lastrowid

    def close(self):
        with self.lock:
            self.db.close()

    def get(self, key, default=None):
        with self.lock:
            row = self._execute("SELECT value FROM documents WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key, value):
        with self.lock, self._write():
            self._execute("INSERT INTO documents(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, allow_nan=False)))

    def settings(self, symbol="US.MU"):
        return {**DEFAULT_SETTINGS, **self.get(stock_key("settings", symbol), {})}

    def save_settings(self, patch, symbol="US.MU"):
        # Concurrent chart changes must not overwrite a separate preference update.
        with self.lock:
            return self._save_settings(patch, stock_symbol(symbol))

    def _save_settings(self, patch, symbol):
        if not isinstance(patch, dict) or set(patch) - set(DEFAULT_SETTINGS):
            raise ValueError("Unknown settings")
        result = {**self.settings(symbol), **patch}
        for key in ("analysis_enabled", "overnight_enabled", "notify_reports"):
            if type(result[key]) is not bool:
                raise ValueError(f"{key} must be true or false")
        for key in ("premarket_interval", "regular_interval", "afterhours_interval", "overnight_interval"):
            if type(result[key]) is not int or result[key] not in (5, 15, 30, 60, 120, 240):
                raise ValueError("Analysis intervals must be 5, 15, 30, 60, 120 or 240 minutes")
        for key in ("alert_above", "alert_below"):
            if result[key] is not None:
                result[key] = number(result[key], key, 0.01)
        if result["alert_above"] and result["alert_below"] and result["alert_below"] >= result["alert_above"]:
            raise ValueError("The lower alert must be below the upper alert")
        if result["timezone"] not in ("America/New_York", "Asia/Singapore"):
            raise ValueError("Unsupported display timezone")
        if result["chart_style"] not in ("line", "area", "candles", "bars") or result["chart_interval"] not in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
            raise ValueError("Unsupported chart preference")
        weights = result["weights"]
        if not isinstance(weights, dict) or set(weights) != set(DEFAULT_SETTINGS["weights"]):
            raise ValueError("All seven factor weights are required")
        result["weights"] = {k: number(v, k, 0) for k, v in weights.items()}
        if abs(sum(result["weights"].values()) - 100) > 0.001:
            raise ValueError("Factor weights must total 100%")
        self.put(stock_key("settings", symbol), result)
        return result

    def upsert_bars(self, bars, symbol="US.MU"):
        values = [(symbol, b["time"], b["interval"], b["open"], b["high"], b["low"], b["close"], b["volume"], b["source"]) for b in bars]
        with self.lock, self._transaction():
            self._executemany("INSERT INTO bars(symbol,time,interval,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,time,interval) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,source=excluded.source", values)

    def bars(self, symbol="US.MU"):
        with self.lock:
            return [dict(r) for r in self._execute("SELECT * FROM bars WHERE symbol=? ORDER BY time,interval", (symbol,))]

    def import_exports(self, directory):
        path = Path(directory) / "US_MU_15m.csv"
        result = {"imported": 0, "rejected": 0}
        if path.exists():
            signature = f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
            if self.get("csv_signature") != signature:
                bars = []
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        try:
                            bars.append(validate_bar(row))
                        except (ValueError, KeyError, OverflowError):
                            result["rejected"] += 1
                self.upsert_bars(bars)
                result["imported"] = len(bars)
                self.put("csv_signature", signature)
                self.put("import_result", result)
        news = {}
        for path in Path(directory).glob("news_*.csv"):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    key = row.get("url") or row.get("title", "")
                    if key:
                        news[key] = {**row, "archived": True, "timing": "Publication date only; year may have been inferred by the legacy importer"}
        self.put("archive_news", sorted(news.values(), key=lambda r: r.get("date", ""), reverse=True)[:200])
        return result

    def new_report(self, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        with self.lock, self._write():
            return self._insert_id("INSERT INTO reports(created,status,body,error) VALUES(?,?,?,?)", (time.time(), "running", json.dumps({"symbol": symbol}), ""))

    def finish_report(self, report_id, body=None, error=None):
        with self.lock, self._transaction():
            row = self._execute("SELECT body FROM reports WHERE id=?", (report_id,)).fetchone()
            symbol = json.loads(row["body"]).get("symbol", "US.MU") if row else "US.MU"
            body = {**(body or {}), "symbol": symbol}
            self._execute("UPDATE reports SET status=?,body=?,error=? WHERE id=?", ("failed" if error else "complete", json.dumps(body or {}, allow_nan=False), str(error or "")[:2000], report_id))

    def cancel_report(self, report_id):
        with self.lock, self._write():
            self._execute("UPDATE reports SET status='cancelled',error='Cancelled by user' WHERE id=? AND status='running'", (report_id,))

    def reports(self, limit=30, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        with self.lock:
            rows = [dict(r) for r in self._execute(f"SELECT * FROM reports WHERE {self.report_symbol_sql}=? ORDER BY id DESC LIMIT ?", (symbol, limit))]
        for row in rows:
            row["body"] = json.loads(row["body"])
        return rows

    def recover_reports(self):
        with self.lock, self._transaction():
            self._execute("UPDATE reports SET status='failed',error='Server restarted before the report completed' WHERE status='running'")

    def event(self, kind, title, body=""):
        with self.lock, self._write():
            self._execute("INSERT INTO events(created,kind,title,body) VALUES(?,?,?,?)", (time.time(), kind, title, body))

    def events(self):
        with self.lock:
            return [dict(r) for r in self._execute("SELECT * FROM events ORDER BY id DESC LIMIT 50")]

    def positions(self, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        with self.lock:
            result = [dict(r) for r in self._execute(f"SELECT * FROM positions WHERE {self.position_symbol_sql}=? ORDER BY id DESC", (symbol,))]
        for row in result:
            row["history"] = json.loads(row["history"])
        return result

    def add_position(self, data, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        quantity = number(data.get("quantity"), "Quantity", 0.000001)
        entry = number(data.get("entry"), "Entry price", 0.01)
        intent = data.get("intent")
        if intent not in ("intraday", "holding"):
            raise ValueError("Choose intraday or holding")
        reason = str(data.get("reason", "")).strip()[:1000]
        if not reason:
            raise ValueError("Record a short reason for this position")
        created = time.time()
        history = [{"symbol": symbol, "time": created, "intent": intent, "reason": reason}]
        with self.lock, self._write():
            result = self._insert_id("INSERT INTO positions(created,quantity,entry,intent,reason,history) VALUES(?,?,?,?,?,?)", (created, quantity, entry, intent, reason, json.dumps(history)))
        return result

    def update_position(self, position_id, data, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        with self.lock, self._transaction():
            row = self._execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
            if not row or row["state"] != "open":
                raise ValueError("Open position not found")
            history = json.loads(row["history"])
            if history[0].get("symbol", "US.MU") != symbol:
                raise ValueError("Position belongs to another stock")
            if data.get("action") == "close":
                exit_price = number(data.get("exit"), "Exit price", 0.01)
                history.append({"time": time.time(), "action": "closed", "exit": exit_price})
                self._execute("UPDATE positions SET state='closed',exit=?,history=? WHERE id=?", (exit_price, json.dumps(history), position_id))
            else:
                intent = data.get("intent")
                reason = str(data.get("reason", "")).strip()[:1000]
                if intent not in ("intraday", "holding") or not reason:
                    raise ValueError("A valid intention and reason are required")
                history.append({"time": time.time(), "intent": intent, "reason": reason})
                self._execute("UPDATE positions SET intent=?,reason=?,history=? WHERE id=?", (intent, reason, json.dumps(history), position_id))

    def tick(self, record, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        sequence = str(record["sequence"]) if symbol == "US.MU" else f"{symbol}:{record['sequence']}"
        with self.lock, self._write():
            self._execute("INSERT INTO ticks(sequence,time,price,volume) VALUES(?,?,?,?) ON CONFLICT(sequence) DO NOTHING", (sequence, record["time"], record["price"], record["volume"]))

    def ticks(self, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        with self.lock:
            return [dict(r) for r in self._execute(f"SELECT * FROM ticks WHERE {self.tick_symbol_sql}=? ORDER BY time DESC LIMIT 30", (symbol,))]
