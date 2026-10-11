"""Stock isolation and source preservation regression checks."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from stork.feed import OpenDFeed
from stork.research import REPORT_SCHEMA, NEW_REPORT_SCHEMA, search_audit, validate_report, validate_explanation, schedule_bucket
from datetime import datetime
from stork.market import NEW_YORK
from stork.storage import DEFAULT_SETTINGS
from stork.server import Application
from stork.storage import Store


class StockIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_settings_and_invalid_symbol(self):
        self.store.save_settings({'alert_above': 100, 'regular_interval': 5})
        self.store.save_settings({'alert_above': 200}, 'US.SNDK')
        self.assertEqual(self.store.settings()['alert_above'], 100)
        self.assertEqual(self.store.settings('US.SNDK')['regular_interval'], 15)
        with self.assertRaises(ValueError):
            self.store.settings('US.BAD')

    def test_reports_keep_symbol_on_failure_and_legacy_defaults_to_mu(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO reports(created,status,body,error) VALUES(0,'complete','{}','')")
        ident = self.store.new_report('US.SNDK')
        self.store.finish_report(ident, error='Test failure')
        self.assertEqual(len(self.store.reports()), 1)
        report = self.store.reports(symbol='US.SNDK')[0]
        self.assertEqual(report['id'], ident)
        self.assertEqual(report['body']['symbol'], 'US.SNDK')

    def test_completed_reports_survive_database_reopening_for_both_stocks(self):
        expected = {}
        for symbol in ('US.MU', 'US.SNDK'):
            ident = self.store.new_report(symbol)
            body = {'headline':symbol, 'summary':'Saved explanation',
                    'beginner':{'summary':'Plain English'},
                    'sources':[{'url':'https://example.com/source'}],
                    'snapshot':{'price':123.456789, 'cutoff':'2026-10-08T10:00:00Z'}}
            self.store.finish_report(ident, body)
            expected[symbol] = self.store.reports(symbol=symbol)[0]
        self.store.close()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        for symbol, report in expected.items():
            self.assertEqual(self.store.reports(symbol=symbol), [report])

    def test_positions_cannot_be_edited_under_other_stock(self):
        data = {'quantity': 2, 'entry': 100, 'intent':'intraday', 'reason':'Test'}
        ident = self.store.add_position(data, 'US.SNDK')
        self.assertEqual(self.store.positions(), [])
        with self.assertRaises(ValueError):
            self.store.update_position(ident, {'action':'close', 'exit':120})
        self.store.update_position(ident, {'intent':'holding','reason':'Updated'}, 'US.SNDK')
        self.assertEqual(self.store.positions('US.SNDK')[0]['history'][0]['intent'], 'intraday')

    def test_tick_sequences_and_live_updates_are_separate(self):
        feed = OpenDFeed(self.store)
        now = time.time()
        for symbol, price in [('US.MU',100),('US.SNDK',200)]:
            feed._tick({'code':symbol,'sequence':42,'time':now,'price':price,'volume':5})
        self.assertEqual(self.store.ticks()[0]['price'], 100)
        self.assertEqual(self.store.ticks('US.SNDK')[0]['price'], 200)
        self.assertEqual(feed.quotes['US.MU']['price'], 100)
        self.assertEqual(feed.quotes['US.SNDK']['price'], 200)

    def test_candle_routing_and_threshold_isolation(self):
        feed = OpenDFeed(self.store)
        for symbol, price in [('US.MU',100),('US.SNDK',200)]:
            feed._bars([{'code':symbol,'time_key':'2026-10-08 10:01:00','open':price,'high':price,'low':price,'close':price,'volume':1}], 1)
        self.assertEqual(self.store.bars()[0]['close'],100)
        self.assertEqual(self.store.bars('US.SNDK')[0]['close'],200)
        self.store.save_settings({'alert_above':150}, 'US.SNDK')
        feed._check_alerts(200)
        self.assertEqual(self.store.events(), [])
        feed._check_alerts(200, 'US.SNDK')
        self.assertIn('SNDK', self.store.events()[0]['title'])

    def test_market_empty_sndk_never_falls_back_to_mu(self):
        app = Application(self.temp.name + '/app', self.temp.name)
        try:
            app.feed.quotes['US.MU'] = {'price':100,'time':time.time(),'reference':99,'session':'regular','source':'test'}
            self.assertIsNone(app.market('US.SNDK')['price'])
            self.assertEqual(app.market()['price'],100)
        finally:
            app.close()


class ReportEvidenceTests(unittest.TestCase):
    def test_scheduler_rejects_stale_selected_stock_even_with_fresh_benchmarks(self):
        settings = {**DEFAULT_SETTINGS, 'analysis_enabled':True}
        feed = {'state':'connected','market_state':'MORNING','age_seconds':1,'symbol':'US.SNDK','quotes':{'US.SNDK':{'time':time.time()-300}}}
        self.assertIsNone(schedule_bucket(settings,feed,datetime.now(NEW_YORK)))

    def report(self):
        return {key: [] if schema['type']=='array' else 'Evidence' for key,schema in REPORT_SCHEMA['properties'].items()}

    def test_all_sources_survive_more_than_thirty_items(self):
        report = self.report()
        report['beginner'] = self.report()
        report['sources'] = [{'title':str(i),'source':'Test','url':f'https://example.com/{i}','published':'Unknown','summary':'Example','relevance':'Background','access':'read','usage':'background'} for i in range(45)]
        validate_report(report, NEW_REPORT_SCHEMA)
        self.assertEqual(len(report['sources']),45)

    def test_beginner_cannot_change_sources_or_direction(self):
        original = self.report()
        original['factors'] = [{'name':'Test','direction':'mixed','evidence':'Test','source_url':'https://example.com'}]
        beginner = copy.deepcopy(original)
        beginner['factors'][0]['direction'] = 'supportive'
        with self.assertRaises(ValueError):
            validate_explanation(original,beginner)

    def test_audit_excludes_messages_and_diagnostics(self):
        output = '\n'.join([json.dumps({'type':'item.completed','item':{'type':'web_search','query':'SNDK news','action':{'type':'search'}}}), json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'private context'}}), 'diagnostic'])
        self.assertEqual(search_audit(output), [{'query':'SNDK news','action':{'type':'search'}}])
