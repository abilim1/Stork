"""Deterministic checks; no OpenD, model calls, or brokerage access required."""
import copy
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from stork.feed import OpenDFeed, normalize_quote
from stork.market import NEW_YORK, aggregate, performance, session_at, timestamp, validate_bar
from stork.research import REPORT_SCHEMA, Research, schedule_bucket, validate_report
from stork.server import Application, Handler
from stork.storage import DEFAULT_SETTINGS, Store


def bar(date="2026-10-07", clock="09:45", close=105, interval=15, volume=100):
    return validate_bar({"time_key": f"{date} {clock}:00", "open": 100, "high": max(110, close), "low": 90, "close": close, "volume": volume}, interval)


class MarketTests(unittest.TestCase):
    def test_end_label_session_boundaries(self):
        for clock, candle_session, trade_session in [("09:30", "premarket", "regular"), ("16:00", "regular", "afterhours"), ("20:00", "afterhours", "overnight")]:
            ts = timestamp(f"2026-10-07T{clock}:00")
            self.assertEqual(session_at(ts, True), candle_session)
            self.assertEqual(session_at(ts), trade_session)

    def test_dst_and_subsecond_timestamps(self):
        self.assertEqual(timestamp("2026-01-07T09:30:00"), timestamp("2026-01-07T14:30:00Z"))
        self.assertEqual(timestamp("2026-07-07T09:30:00"), timestamp("2026-07-07T13:30:00Z"))
        self.assertAlmostEqual(timestamp("2026-10-07T10:00:00.250") % 1, .25)

    def test_no_double_count_from_overlapping_resolutions(self):
        rows = [bar(clock="09:45", volume=900), bar(clock="09:44", interval=1), bar(clock="09:45", interval=1), bar(clock="09:46", interval=1, close=108)]
        result = aggregate(rows, "30m", "regular")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["volume"], 1000)
        self.assertEqual(result[0]["close"], 108)

    def test_session_aligned_hour_does_not_mix_premarket(self):
        rows = [bar(clock="09:30"), bar(clock="09:45"), bar(clock="10:15"), bar(clock="10:45")]
        result = aggregate(rows, "1h")
        self.assertEqual([r["session"] for r in result], ["premarket", "regular", "regular"])
        self.assertEqual([r["volume"] for r in result], [100, 200, 100])

    def test_never_invents_finer_bars(self):
        self.assertEqual(aggregate([bar()], "1m"), [])

    def test_rejects_invalid_prices(self):
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                validate_bar({"time_key": "2026-10-07T09:45", "open": 100, "high": 110, "low": 90, "close": value, "volume": 1})

    def test_weekly_reference_is_preceding_week_close(self):
        result = performance([bar("2026-10-02", "16:00", 100), bar("2026-10-05", "16:00", 110)])
        self.assertIsNone(result["weekly"][0]["return_pct"])
        self.assertAlmostEqual(result["weekly"][1]["return_pct"], 10)
        self.assertTrue(result["weekly"][1]["partial"])

    def test_friday_record_requires_observed_thursday_and_full_friday(self):
        rows = [bar("2026-10-01", "16:00", 100), bar("2026-10-02", "16:00", 95), bar("2026-10-09", "12:00", 90)]
        record = performance(rows)["friday"]
        self.assertEqual(record["sample_size"], 1)
        self.assertEqual(record["negative_count"], 1)
        self.assertAlmostEqual(record["mean_return_pct"], -5)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_preferences_persist_and_bad_weights_are_atomic(self):
        self.store.save_settings({"regular_interval": 30})
        bad = dict(DEFAULT_SETTINGS["weights"], company=99)
        with self.assertRaises(ValueError):
            self.store.save_settings({"regular_interval": 60, "weights": bad})
        self.assertEqual(self.store.settings()["regular_interval"], 30)
        other = Store(Path(self.temp.name) / "test.sqlite3")
        self.assertEqual(other.settings()["regular_interval"], 30)
        other.close()

    def test_reject_invalid_settings(self):
        for value in ({"analysis_enabled": "false"}, {"regular_interval": True}, {"alert_above": float("nan")}, {"alert_above": 99, "alert_below": 100}, {"timezone": "bad"}):
            with self.assertRaises(ValueError):
                self.store.save_settings(value)

    def test_original_intention_survives_change_and_close(self):
        ident = self.store.add_position({"quantity": 2, "entry": 100, "intent": "intraday", "reason": "Original plan"})
        self.store.update_position(ident, {"intent": "holding", "reason": "New evidence"})
        self.store.update_position(ident, {"action": "close", "exit": 110})
        position = self.store.positions()[0]
        self.assertEqual(position["state"], "closed")
        self.assertEqual(position["history"][0]["intent"], "intraday")
        self.assertEqual(position["history"][1]["reason"], "New evidence")
        self.assertEqual(len(position["history"]), 3)
        with self.assertRaises(ValueError):
            self.store.update_position(ident, {"action": "close", "exit": 120})

    def test_replayed_ticks_are_deduplicated(self):
        tick = {"sequence": "a", "time": 1.25, "price": 100, "volume": 2}
        self.store.tick(tick)
        self.store.tick(tick)
        self.assertEqual(len(self.store.ticks()), 1)
        self.assertEqual(self.store.ticks()[0]["time"], 1.25)

    def test_older_quotes_cannot_replace_newer_trade(self):
        feed = OpenDFeed(self.store)
        feed.status["market_state"] = "MORNING"
        feed.quotes["US.MU"] = {"time": timestamp("2026-10-07T10:01:00"), "price": 108}
        feed._quotes([{"code": "US.MU", "last_price": 101, "prev_close_price": 100, "update_time": "2026-10-07 10:00:00"}])
        self.assertEqual(feed.quotes["US.MU"]["price"], 108)

    def test_out_of_order_tick_cannot_trigger_alert(self):
        self.store.save_settings({"alert_above": 110})
        feed = OpenDFeed(self.store)
        now = timestamp("2026-10-07T10:01:00")
        feed.quotes["US.MU"] = {"time": now, "price": 108, "session": "regular", "reference": 100}
        with patch("stork.feed.time.time", return_value=now):
            feed._tick({"sequence": "older", "time": now - 10, "price": 120, "volume": 1})
        self.assertEqual(self.store.events(), [])
        self.assertEqual(feed.quotes["US.MU"]["price"], 108)
        self.assertEqual(len(self.store.ticks()), 1)

    def test_alert_only_on_threshold_crossing(self):
        self.store.save_settings({"alert_above": 110})
        feed = OpenDFeed(self.store)
        for price in (109, 110, 111, 109, 111):
            feed._check_alerts(price)
        self.assertEqual(len(self.store.events()), 2)


class ResearchTests(unittest.TestCase):
    def test_quote_uses_extended_price_and_correct_reference(self):
        row = {"code": "US.MU", "last_price": 110, "prev_close_price": 100, "overnight_price": 108, "update_time": "2026-10-07 20:00:00"}
        overnight = normalize_quote(row, "OVERNIGHT")
        self.assertEqual(overnight["price"], 108)
        self.assertEqual(overnight["reference"], 110)
        self.assertIsNone(normalize_quote(row, "PRE_MARKET_BEGIN"))
        self.assertEqual(normalize_quote(row, "MORNING")["reference"], 100)

    def test_premarket_does_not_use_the_two_sessions_old_close(self):
        row = {"code": "US.MU", "last_price": 1088, "prev_close_price": 1045.56, "pre_price": 1071.96, "update_time": "2026-10-08 05:28:00"}
        quote = normalize_quote(row, "PRE_MARKET_BEGIN")
        self.assertEqual(quote["reference"], 1088)
        self.assertLess(quote["change_pct"], 0)
        self.assertAlmostEqual(quote["change_pct"], -1.47426470588)

    def test_report_shape_is_validated(self):
        report = {key: [] if schema["type"] == "array" else "Evidence" for key, schema in REPORT_SCHEMA["properties"].items()}
        validate_report(report)
        report["news"] = [{"title": "Missing fields"}]
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_api_credentials_are_removed(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "example", "CODEX_API_KEY": "example", "OPENAI_BASE_URL": "example", "STORK_DATABASE_URL": "private", "PGPASSWORD": "private", "PGPASSFILE": "private", "PGSERVICEFILE": "private"}):
            env = Research._environment()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertNotIn("OPENAI_BASE_URL", env)
        for key in ("STORK_DATABASE_URL", "PGPASSWORD", "PGPASSFILE", "PGSERVICEFILE"):
            self.assertNotIn(key, env)

    def test_scheduler_obeys_feed_and_session(self):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings["analysis_enabled"] = True
        feed = {"state": "connected", "market_state": "MORNING", "age_seconds": 1}
        now = datetime(2026, 10, 7, 10, 20, tzinfo=NEW_YORK)
        self.assertEqual(schedule_bucket(settings, feed, now), "2026-10-07:regular:41")
        self.assertIsNone(schedule_bucket(settings, {**feed, "market_state": "CLOSED"}, now))
        self.assertIsNone(schedule_bucket(settings, {**feed, "age_seconds": 200}, now))
        self.assertIsNone(schedule_bucket(settings, {**feed, "market_state": "OVERNIGHT"}, now))
        settings["overnight_enabled"] = True
        sunday = datetime(2026, 10, 4, 21, tzinfo=NEW_YORK)
        self.assertIsNotNone(schedule_bucket(settings, {**feed, "market_state": "OVERNIGHT"}, sunday))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = Application(cls.temp.name, cls.temp.name)
        cls.app.token = ""
        cls.app.store.upsert_bars([bar()])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.app = cls.app
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.close()
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        h = {"Content-Type": "application/json", "X-Stork-Request": "1", **(headers or {})}
        connection.request(method, path, json.dumps(body) if body is not None else None, h)
        response = connection.getresponse()
        status, payload = response.status, response.read()
        connection.close()
        return status, payload

    def test_state_and_finer_history_warning(self):
        status, payload = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(payload)["market"]["live"])
        status, payload = self.request("GET", "/api/candles?interval=1m")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["warning"])

    def test_empty_session_date_is_not_a_resolution_gap(self):
        with patch.object(self.app.store, "bars", return_value=[bar(), bar("2026-10-08", "02:00")]):
            result = self.app.candles({"interval": ["30m"], "session": ["regular"]})
        self.assertEqual(result["warning"], "")
        self.assertEqual(len(result["candles"]), 1)

    def test_saved_afterhours_uses_same_day_regular_close(self):
        rows = [bar("2026-10-06", "16:00", 100), bar("2026-10-07", "16:00", 110), bar("2026-10-07", "18:00", 115)]
        with patch.object(self.app.store, "bars", return_value=rows):
            result = self.app.market()
        self.assertEqual(result["reference"], 110)
        self.assertEqual(result["session"], "afterhours")
        self.assertFalse(result["live"])
        self.assertAlmostEqual(result["change_pct"], (115 / 110 - 1) * 100)

    def test_saved_regular_close_uses_preceding_day(self):
        rows = [bar("2026-10-06", "16:00", 100), bar("2026-10-07", "16:00", 110)]
        with patch.object(self.app.store, "bars", return_value=rows):
            result = self.app.market()
        self.assertEqual(result["reference"], 100)
        self.assertEqual(result["session"], "regular")

    def test_theme_script_is_served(self):
        status, payload = self.request("GET", "/theme.js")
        self.assertEqual(status, 200)
        self.assertIn(b"stork-theme", payload)

    def test_invalid_requests_do_not_change_preferences(self):
        status, _ = self.request("POST", "/api/settings", {"analysis_enabled": "yes"})
        self.assertEqual(status, 400)
        self.assertFalse(self.app.store.settings()["analysis_enabled"])

    def test_origin_and_host_and_write_verification(self):
        self.assertEqual(self.request("POST", "/api/settings", {}, {"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/settings", {}, {"X-Stork-Request": ""})[0], 403)

    def test_remote_access_token(self):
        self.app.token = "test-token"
        try:
            self.assertEqual(self.request("GET", "/api/state")[0], 401)
            self.assertEqual(self.request("GET", "/api/state", headers={"Authorization": "Bearer test-token"})[0], 200)
        finally:
            self.app.token = ""

    def test_mcp_tools_are_read_only_and_notifications_have_no_body(self):
        status, payload = self.request("POST", "/api/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 200)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in json.loads(payload)["result"]["tools"]))
        self.assertEqual(self.request("POST", "/api/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}), (202, b""))

    def test_stock_queries_isolate_settings_history_and_archived_news(self):
        status, payload = self.request("GET", "/api/state?symbol=US.SNDK")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["stock"]["ticker"], "SNDK")
        self.assertIsNone(json.loads(payload)["market"]["price"])
        self.assertEqual(self.request("POST", "/api/settings?symbol=US.SNDK", {"alert_above": 222})[0], 200)
        self.assertIsNone(self.app.store.settings()["alert_above"])
        self.assertEqual(self.app.store.settings("US.SNDK")["alert_above"], 222)
        self.assertEqual(json.loads(self.request("GET", "/api/news?symbol=US.SNDK")[1])["items"], [])
        self.assertEqual(self.request("GET", "/api/state?symbol=US.BAD")[0], 400)

    def test_beginner_request_passes_report_and_stock(self):
        with patch.object(self.app.research, "explain", return_value=7) as explain:
            status, payload = self.request("POST", "/api/research/beginner?symbol=US.SNDK", {"id": 7})
        self.assertEqual(status, 200)
        explain.assert_called_once_with(7, "US.SNDK")

    def test_cancel_request_targets_exact_run_and_uses_write_security(self):
        with patch.object(self.app.research, "cancel", return_value={"accepted":True}) as cancel:
            status, payload = self.request("POST", "/api/research/cancel?symbol=US.SNDK", {"run_id":"active-run"})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(payload)["accepted"])
            cancel.assert_called_once_with("active-run", "US.SNDK")
            self.assertEqual(self.request("POST", "/api/research/cancel", {"run_id":"active-run"}, {"Origin":"https://example.org"})[0],403)
            self.assertEqual(self.request("POST", "/api/research/cancel", {})[0],400)
        with patch.object(self.app.research, "cancel", side_effect=ValueError("Run changed")):
            self.assertEqual(self.request("POST", "/api/research/cancel", {"run_id":"old-run"})[0],400)


if __name__ == "__main__":
    unittest.main()
