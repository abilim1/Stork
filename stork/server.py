"""Small private web service; bind to loopback behind an authenticated TLS proxy in production."""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .feed import OpenDFeed, SESSION_STATES
from .market import NEW_YORK, aggregate, change, local_day, performance, session_at
from .research import Research
from .storage import Store
from .stocks import STOCKS, stock_symbol

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class Application:
    def __init__(self, data_dir, exports, opend_host="127.0.0.1", opend_port=11111, database_url=None):
        if database_url:
            from .postgres import PostgreSQLStore
            self.store = PostgreSQLStore(database_url)
        else:
            self.store = Store(Path(data_dir) / "stork.sqlite3")
        self.store.import_exports(exports)
        self.feed = OpenDFeed(self.store, opend_host, opend_port)
        self.research = Research(self.store, self.feed, data_dir, self.research_snapshot)
        self.token = os.environ.get("STORK_ACCESS_TOKEN", "")
        self.schedule_thread = threading.Thread(target=self.research.scheduler, daemon=True)
        self.schedule_thread.start()

    def close(self):
        self.research.stop.set()
        self.research.cancel_event.set()
        self.schedule_thread.join(timeout=2)
        self.feed.disconnect()
        if self.research.thread and self.research.thread.is_alive():
            self.research.thread.join(timeout=20)
        if not self.research.thread or not self.research.thread.is_alive():
            self.store.close()

    def market(self, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        rows = self.store.bars(symbol)
        perf = performance(rows)
        feed = self.feed.snapshot(symbol)
        quote = feed["quotes"].get(symbol)
        latest = rows[-1] if rows else None
        if quote:
            price = quote["price"]
            asof = quote["time"]
            reference = quote.get("reference")
            live = feed["state"] == "connected" and feed["market_state"] in SESSION_STATES and asof is not None and -5 <= time.time() - asof < 120
        else:
            price, asof = (latest["close"], latest["time"]) if latest else (None, None)
            observed_session = session_at(asof, True) if asof is not None else "historical"
            # Extended prices compare with the most recent completed regular session.
            # An end-labelled 16:00 candle itself still uses the preceding close.
            previous = [d for d in perf["daily"] if latest and d["time"] < asof and (observed_session in ("afterhours", "overnight") or d["date"] < local_day(asof))]
            reference = previous[-1]["close"] if previous else None
            live = False
        price_session = quote["session"] if quote else observed_session
        regular_today = [d for d in perf["daily"] if asof and d["date"] == local_day(asof)]
        day = regular_today[-1] if regular_today else None
        return {"price": price, "asof": asof, "reference": reference, "change_pct": change(price, reference), "session": price_session, "live": live, "source": quote["source"] if quote else "Historical CSV / saved candles", "regular_open": day["open"] if day else None, "from_open_pct": change(price, day["open"]) if day else None, "day": day, "daily": perf["daily"], "weekly": perf["weekly"], "friday": perf["friday"], "bar_count": len(rows), "last_bar_time": latest["time"] if latest else None, "feed": feed}

    def research_snapshot(self, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        market = self.market(symbol)
        return {"stock": STOCKS[symbol], "now_utc": datetime.now(timezone.utc).isoformat(), "market": market, "factor_priorities": self.store.settings(symbol)["weights"], "limitations": ["Weights are unvalidated editorial priorities, not probability estimates.", "Historical candles are QFQ-adjusted and end-labelled in New York time.", "Incomplete sessions/weeks must not be described as completed returns.", "Benchmark percent changes use the reference and session explicitly supplied by each quote.", "No predictive model has been fitted or validated."]}

    def state(self, symbol="US.MU"):
        symbol = stock_symbol(symbol)
        return {"stock": STOCKS[symbol], "stocks": list(STOCKS.values()), "market": self.market(symbol), "settings": self.store.settings(symbol), "research": self.research.progress(), "reports": self.store.reports(symbol=symbol), "positions": self.store.positions(symbol), "events": self.store.events(), "ticks": self.store.ticks(symbol), "import": self.store.get("import_result", {}), "server_time": time.time()}

    def candles(self, query):
        symbol = stock_symbol(query.get("symbol", ["US.MU"])[0])
        interval, session = query.get("interval", ["15m"])[0], query.get("session", ["all"])[0]
        span = query.get("range", ["5D"])[0]
        rows = self.store.bars(symbol)
        days = sorted({local_day(r["time"] - 1) for r in rows})
        counts = {"1D": 1, "5D": 5, "1M": 22, "3M": 66, "ALL": len(days)}
        if span == "custom":
            start, end = query.get("start", [""])[0], query.get("end", [""])[0]
            try:
                start = datetime.strptime(start, "%Y-%m-%d").date().isoformat()
                end = datetime.strptime(end, "%Y-%m-%d").date().isoformat()
            except ValueError as exc:
                raise ValueError("Choose valid start and end dates") from exc
            if start > end:
                raise ValueError("Start must precede end")
            rows = [r for r in rows if start <= local_day(r["time"] - 1) <= end]
        elif span in counts:
            selected = set(days[-counts[span]:])
            rows = [r for r in rows if local_day(r["time"] - 1) in selected]
        else:
            raise ValueError("Unsupported date range")
        result = aggregate(rows, interval, session)
        raw_days = {local_day(r["time"] - 1) for r in rows if session == "all" or session_at(r["time"], True) == session}
        result_days = {r["date"] for r in result}
        warning = ""
        if not result:
            warning = "No candles at this resolution for the selected period and session. Try 15 minutes or a wider date range."
        elif raw_days - result_days:
            warning = "This resolution is available for only part of the selected history. No finer candles have been invented."
        if any(r["session"] == "overnight" for r in result):
            warning += (" " if warning else "") + "Overnight history is collected while connected; gaps may be present."
        return {"candles": result, "warning": warning, "interval": interval, "session": session, "range": span, "timezone": "America/New_York", "timestamp_convention": "end", "adjustment": "QFQ", "available_days": len(days)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.address_string(), fmt % args)

    def respond(self, status, body, content_type="application/json; charset=utf-8"):
        payload = json.dumps(body, allow_nan=False).encode() if isinstance(body, (dict, list)) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self, mutation=False):
        host = self.headers.get("Host", "")
        if self.server.server_address[0] in ("127.0.0.1", "::1") and host.split(":")[0] not in ("localhost", "127.0.0.1", "[::1]"):
            self.respond(403, {"error": "Host is not permitted"})
            return False
        if self.app.token and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.app.token):
            self.respond(401, {"error": "Enter the server access token to connect"})
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != host:
            self.respond(403, {"error": "Cross-origin requests are not permitted"})
            return False
        if mutation and self.headers.get("X-Stork-Request") != "1" and not self.app.token:
            self.respond(403, {"error": "Missing request verification header"})
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.authorized():
                return
            try:
                symbol = stock_symbol(parse_qs(parsed.query).get("symbol", ["US.MU"])[0])
                if parsed.path == "/api/state":
                    result = self.app.state(symbol)
                elif parsed.path == "/api/candles":
                    result = self.app.candles(parse_qs(parsed.query))
                elif parsed.path == "/api/news":
                    result = {"items": self.app.store.get("archive_news", []) if symbol == "US.MU" else []}
                elif parsed.path == "/api/auth-status":
                    result = self.app.research.auth_status()
                elif parsed.path == "/api/mcp":
                    return self.respond(405, {"error": "Use POST; this endpoint does not provide an SSE stream"})
                else:
                    return self.respond(404, {"error": "Not found"})
                self.respond(200, result)
            except ValueError as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                logging.exception("Read request failed")
                self.respond(500, {"error": "Unable to read this resource. Check the server log."})
            return
        paths = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/chart.js": ("chart.js", "text/javascript; charset=utf-8"), "/reports.js": ("reports.js", "text/javascript; charset=utf-8"), "/theme.js": ("theme.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
        if parsed.path == "/favicon.ico":
            return self.respond(204, b"")
        if parsed.path not in paths:
            return self.respond(404, {"error": "Not found"})
        name, mime = paths[parsed.path]
        self.respond(200, (WEB / name).read_bytes(), mime)

    def do_POST(self):
        if not self.authorized(mutation=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Request body must be between 1 and 65536 bytes")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("JSON content is required")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            parsed = urlparse(self.path)
            path = parsed.path
            symbol = stock_symbol(parse_qs(parsed.query).get("symbol", ["US.MU"])[0])
            if path == "/api/settings":
                result = self.app.store.save_settings(data, symbol)
            elif path == "/api/feed/connect":
                result = self.app.feed.connect()
            elif path == "/api/feed/disconnect":
                self.app.feed.disconnect()
                result = {"ok": True}
            elif path == "/api/research/run":
                result = {"id": self.app.research.start(symbol)}
            elif path == "/api/research/beginner":
                result = {"id": self.app.research.explain(int(data["id"]), symbol)}
            elif path == "/api/research/cancel":
                result = self.app.research.cancel(str(data["run_id"]), symbol)
            elif path == "/api/positions":
                result = {"id": self.app.store.add_position(data, symbol)}
            elif path.startswith("/api/positions/"):
                self.app.store.update_position(int(path.rsplit("/", 1)[-1]), data, symbol)
                result = {"ok": True}
            elif path == "/api/mcp":
                result = self.mcp(data)
                if result is None:
                    return self.respond(202, b"")
            else:
                return self.respond(404, {"error": "Not found"})
            self.respond(200, result)
        except (ValueError, TypeError, KeyError) as exc:
            self.respond(400, {"error": str(exc)})
        except Exception:
            logging.exception("Write request failed")
            self.respond(500, {"error": "Request failed. Check the server log."})

    def mcp(self, request):
        if request.get("jsonrpc") != "2.0":
            raise ValueError("JSON-RPC 2.0 is required")
        if "id" not in request:
            return None
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "stork", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": description, "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True}} for name, description in [("get_mu_snapshot", "Read timestamped MU prices, benchmark quotes, session and freshness."), ("get_mu_reports", "Read the last 10 saved MU research reports with cited sources."), ("get_sndk_snapshot", "Read timestamped SNDK market data."), ("get_sndk_reports", "Read saved SNDK research reports.")]]}
        elif method == "ping":
            result = {}
        elif method == "tools/call":
            name = request.get("params", {}).get("name")
            if name not in ("get_mu_snapshot", "get_mu_reports", "get_sndk_snapshot", "get_sndk_reports"):
                return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32602, "message": "Unknown tool"}}
            symbol = "US.SNDK" if name.startswith("get_sndk") else "US.MU"
            value = self.app.research_snapshot(symbol) if name.endswith("snapshot") else self.app.store.reports(10, symbol)
            result = {"content": [{"type": "text", "text": json.dumps(value)}]}
        elif method == "notifications/initialized":
            return None
        else:
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601, "message": "Method not found"}}
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


def main():
    parser = argparse.ArgumentParser(description="Stork private market dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".stork")
    parser.add_argument("--exports", type=Path, default=ROOT / "saves")
    parser.add_argument("--opend-host", default=os.environ.get("STORK_OPEND_HOST", "127.0.0.1"))
    parser.add_argument("--opend-port", type=int, default=int(os.environ.get("STORK_OPEND_PORT", "11111")))
    parser.add_argument("--connect-live", action="store_true")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1") and len(os.environ.get("STORK_ACCESS_TOKEN", "")) < 32:
        parser.error("Remote binding requires STORK_ACCESS_TOKEN of at least 32 characters and an HTTPS reverse proxy")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = Application(args.data_dir, args.exports, args.opend_host, args.opend_port,
                      database_url=os.environ.get("STORK_DATABASE_URL"))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app
    if args.connect_live:
        app.feed.connect()
    print(f"Stork is available at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
