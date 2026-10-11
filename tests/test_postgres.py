"""Real PostgreSQL integration checks, using disposable databases only.

Set STORK_TEST_DATABASE_URL to an administrator connection whose database is
named stork_test_control. Never point this suite at the live Stork database.
"""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from uuid import uuid4

from stork.migrate_postgres import migrate
from stork.storage import Store


@unittest.skipUnless(os.environ.get("STORK_TEST_DATABASE_URL"), "Isolated PostgreSQL test connection not configured")
class PostgreSQLTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from stork.postgres import PostgreSQLStore
        self.store_class = PostgreSQLStore
        config = conninfo_to_dict(os.environ["STORK_TEST_DATABASE_URL"])
        if config.get("dbname") != "stork_test_control":
            raise ValueError("Tests require the disposable stork_test_control database")
        self.admin = psycopg.connect(**config, autocommit=True)
        self.database = "stork_test_" + uuid4().hex
        self.admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(self.database)))
        self.conninfo = make_conninfo(**{**config, "dbname": self.database})
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source.sqlite3"
        self.backup = Path(self.temp.name) / "backup.sqlite3"
        self.store = None

    def tearDown(self):
        from psycopg import sql
        if self.store:
            self.store.close()
        self.admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(self.database)))
        self.admin.close()
        self.temp.cleanup()

    def open(self):
        self.store = self.store_class(self.conninfo)
        return self.store

    def test_reports_preserve_sources_modes_and_stock_after_restart(self):
        store = self.open()
        ids = []
        for symbol in ("US.MU", "US.SNDK"):
            ident = store.new_report(symbol)
            body = {"summary": "test 12.3456789", "sources": [{"url": "https://example.com/?a=1&b=2"}],
                    "beginner": {"summary": "Plain English"}, "snapshot": {"price": 123.456789}}
            store.finish_report(ident, body)
            store.cancel_report(ident)
            self.assertEqual(store.reports(symbol=symbol)[0]["status"], "complete")
            ids.append(ident)
        store.close()
        store = self.open()
        for symbol, ident in zip(("US.MU", "US.SNDK"), ids):
            result = store.reports(symbol=symbol)[0]
            self.assertEqual(result["id"], ident)
            self.assertEqual(result["body"]["snapshot"]["price"], 123.456789)
        pending = store.new_report("US.SNDK")
        store.cancel_report(pending)
        self.assertEqual(store.reports(symbol="US.SNDK")[0]["status"], "cancelled")
        store.new_report()
        store.recover_reports()
        self.assertEqual(store.reports()[0]["status"], "failed")

    def test_settings_positions_and_invalid_write_recovery(self):
        import psycopg
        store = self.open()
        store.save_settings({"regular_interval": 30})
        store.save_settings({"regular_interval": 60}, "US.SNDK")
        self.assertEqual(store.settings()["regular_interval"], 30)
        self.assertEqual(store.settings("US.SNDK")["regular_interval"], 60)
        with self.assertRaises(ValueError):
            store.save_settings({"weights": {"company": 999}})
        self.assertEqual(store.settings()["regular_interval"], 30)
        with self.assertRaises(psycopg.Error), store._transaction():
            store._execute("INSERT INTO documents(key,value) VALUES(?,?)", ("bad", None))
        store.put("valid", {"value": "quotes ' ? % : 😀"})
        self.assertEqual(store.get("valid")["value"], "quotes ' ? % : 😀")
        ident = store.add_position({"quantity": 1.25, "entry": 12.345, "intent": "intraday", "reason": "original"}, "US.SNDK")
        with self.assertRaises(ValueError):
            store.update_position(ident, {"action": "close", "exit": 15}, "US.MU")
        store.update_position(ident, {"intent": "holding", "reason": "changed"}, "US.SNDK")
        store.update_position(ident, {"action": "close", "exit": 15}, "US.SNDK")
        position = store.positions("US.SNDK")[0]
        self.assertEqual(position["state"], "closed")
        self.assertEqual(len(position["history"]), 3)
        self.assertEqual(position["history"][0]["intent"], "intraday")
        self.assertEqual(store.positions(), [])

    def test_bars_upsert_ticks_deduplication_and_concurrent_writers(self):
        store = self.open()
        bar = {"time": 1791000000, "interval": 1, "open": 1, "high": 3, "low": 1, "close": 2, "volume": 5, "source": "test"}
        for symbol in ("US.MU", "US.SNDK"):
            store.upsert_bars([bar], symbol)
            store.upsert_bars([{**bar, "close": 3}], symbol)
            self.assertEqual(len(store.bars(symbol)), 1)
            self.assertEqual(store.bars(symbol)[0]["close"], 3)
        errors = []
        def write(symbol):
            try:
                for _ in range(2):
                    for sequence in range(40):
                        store.tick({"sequence": sequence, "time": sequence, "price": 1.2345, "volume": 2}, symbol)
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=write, args=(s,)) for s in ("US.MU", "US.SNDK")]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        self.assertEqual(errors, [])
        self.assertEqual(store._execute("SELECT COUNT(*) AS n FROM ticks").fetchone()["n"], 80)
        self.assertEqual(len(store.ticks("US.SNDK")), 30)
        self.assertEqual(store.ticks()[0]["time"], 39)

    def test_second_app_and_migration_cannot_share_running_database(self):
        self.open()
        with self.assertRaisesRegex(RuntimeError, "Another Stork"):
            self.store_class(self.conninfo)
        Store(self.source).close()
        with self.assertRaisesRegex(RuntimeError, "Another Stork"):
            migrate(self.source, self.backup, self.conninfo)

    def test_migration_preserves_all_content_and_advances_ids(self):
        source = Store(self.source)
        for symbol in ("US.MU", "US.SNDK"):
            source.save_settings({"regular_interval": 60}, symbol)
            source.finish_report(source.new_report(symbol), {"summary": "Quotes ' ? % 😀", "beginner": {"summary": "easy"}})
            source.add_position({"quantity": 2, "entry": 11.12345, "intent": "holding", "reason": "test"}, symbol)
            source.tick({"sequence": "a:Z" if symbol == "US.SNDK" else "aZ", "time": 1.125, "price": 123.456789, "volume": 7}, symbol)
        source.event("test", "preserved", "body")
        source._execute("INSERT INTO reports(id,created,status,body,error) VALUES(99,0,'complete','{}','')")
        source.db.commit()
        source.close()
        manifest = migrate(self.source, self.backup, self.conninfo)
        self.assertEqual(manifest["reports"]["count"], 3)
        self.assertEqual(manifest["ticks"]["count"], 2)
        with closing(sqlite3.connect(self.backup)) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 3)
        store = self.open()
        self.assertEqual(store.reports()[0]["id"], 99)
        self.assertEqual(store.new_report(), 100)
        self.assertEqual(store.add_position({"quantity": 1, "entry": 1, "intent": "holding", "reason": "new"}), 3)
        store.event("new", "new")
        self.assertEqual(store.events()[0]["id"], 2)

    def test_existing_destination_and_backup_are_never_overwritten(self):
        Store(self.source).close()
        store = self.open()
        store.put("existing", {"keep": True})
        store.close()
        self.store = None
        with self.assertRaisesRegex(ValueError, "not empty"):
            migrate(self.source, self.backup, self.conninfo)
        self.backup.write_text("preserved")
        with self.assertRaises(FileExistsError):
            migrate(self.source, self.backup, self.conninfo)
        self.assertEqual(self.backup.read_text(), "preserved")
        self.assertEqual(self.open().get("existing"), {"keep": True})

    def test_failed_copy_rolls_back_entire_destination(self):
        import psycopg
        source = Store(self.source)
        source.put("valid", {"data": "must roll back"})
        source._execute("INSERT INTO reports(created,status,body,error) VALUES(0,'complete','invalid json','')")
        source.db.commit()
        source.close()
        with self.assertRaises(psycopg.Error):
            migrate(self.source, self.backup, self.conninfo)
        with psycopg.connect(self.conninfo) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0], 0)

    def test_application_uses_postgres_and_keeps_stock_state(self):
        from stork.server import Application
        app = Application(Path(self.temp.name) / "app", self.temp.name, database_url=self.conninfo)
        try:
            app.store.finish_report(app.store.new_report("US.SNDK"), {"summary": "Saved in PostgreSQL"})
            self.assertEqual(len(app.state("US.SNDK")["reports"]), 1)
            self.assertEqual(app.state("US.MU")["reports"], [])
            self.assertFalse((Path(self.temp.name) / "app" / "stork.sqlite3").exists())
        finally:
            app.close()
