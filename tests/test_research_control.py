"""Research lifecycle checks use disposable processes, never paid model calls."""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from stork.feed import OpenDFeed
from stork.research import Research, ResearchCancelled, REPORT_SCHEMA, schedule_bucket
from stork.market import NEW_YORK
from datetime import datetime
from stork.storage import Store


class ResearchControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.feed = OpenDFeed(self.store)
        self.research = Research(self.store, self.feed, self.temp.name, lambda symbol: {})
        self.research.executable = sys.executable
        self.ident = self.store.new_report()
        self.research._begin('US.MU', self.ident, 'research')

    def tearDown(self):
        self.research.cancel_event.set()
        if self.research.thread:
            self.research.thread.join(15)
        self.store.close()
        self.temp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(predicate())

    def body(self):
        return {k: [] if v['type'] == 'array' else 'Evidence' for k, v in REPORT_SCHEMA['properties'].items()}

    def test_progress_is_observable_and_does_not_publish_reasoning(self):
        r = self.research
        for line in ('diagnostic', 'null', '[]', '{"item":null}', json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'private reasoning'}})):
            r._progress_event(line)
        r._progress_event(json.dumps({'type':'item.completed','item':{'type':'web_search','action':{'queries':['MU news','memory pricing'],'url':'https://example.com'}}}))
        state = r.progress()
        self.assertEqual(state['search_count'], 1)
        self.assertIn('memory pricing', state['latest_query'])
        self.assertNotIn('private reasoning', json.dumps(state))
        r.cancel(state['run_id'], 'US.MU')
        r._progress_event('{"type":"turn.completed"}')
        self.assertEqual(r.progress()['stage'], 'Cancelling')

    def test_stale_cancel_cannot_stop_another_run(self):
        r = self.research
        with self.assertRaises(ValueError):
            r.cancel('old-run', 'US.MU')
        with self.assertRaises(ValueError):
            r.cancel(r.status['run_id'], 'US.SNDK')
        self.assertFalse(r.cancel_event.is_set())

    def test_cancel_before_process_start_and_explanation_preservation(self):
        r = self.research
        self.store.finish_report(self.ident, self.body())
        original = self.store.reports()[0]
        r.cancel(r.status['run_id'], 'US.MU')
        with self.assertRaises(ResearchCancelled):
            r._execute([sys.executable, '-c', 'raise RuntimeError("must not launch")'])
        r._failure(self.ident, ResearchCancelled(), explanation=True)
        self.assertEqual(self.store.reports()[0], original)
        self.assertEqual(r.status['state'], 'cancelled')

    def test_timeout_reaps_process_and_worker_can_be_reused(self):
        r = self.research
        with self.assertRaises(subprocess.TimeoutExpired):
            r._execute([sys.executable, '-c', 'import time;time.sleep(60)'], timeout=.3)
        self.assertIsNone(r.process)
        result = r._execute([sys.executable, '-c', 'print("ready")'], timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn('ready', result.stdout)

    def test_cancel_stops_owned_parent_and_child(self):
        r = self.research
        child = 'import socket,time;s=socket.socket();s.bind(("127.0.0.1",0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(60)'
        parent = ('import subprocess,sys,json,time;'
                  f'p=subprocess.Popen([sys.executable,"-c",{child!r}],stdout=subprocess.PIPE,text=True);'
                  'port=p.stdout.readline().strip();'
                  'print(json.dumps({"type":"item.completed","item":{"type":"web_search","query":port}}),flush=True);'
                  'time.sleep(60)')
        errors = []
        def run():
            try:
                r._execute([sys.executable, '-c', parent], events=True)
            except Exception as exc:
                errors.append(exc)
                r._failure(self.ident, exc)
        r.thread = threading.Thread(target=run)
        r.thread.start()
        self.wait_for(lambda: r.status['latest_query'])
        port = int(r.status['latest_query'])
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            pass
        r.cancel(r.status['run_id'], 'US.MU')
        r.thread.join(15)
        self.assertFalse(r.thread.is_alive())
        self.assertIsInstance(errors[0], ResearchCancelled)
        self.assertEqual(self.store.reports()[0]['status'], 'cancelled')
        self.assertFalse(r.progress()['busy'])
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=1)

    def test_cancel_during_validation_never_commits_output(self):
        r = self.research
        body = {**self.body(), 'beginner':self.body(), 'sources':[]}
        def execute(args, prompt, folder, events):
            (folder / 'report.json').write_text(json.dumps(body))
            r.cancel(r.status['run_id'], 'US.MU')
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(r, '_worker_auth'), patch.object(r, '_execute', side_effect=execute):
            r._run(self.ident, 'US.MU')
        self.assertEqual(self.store.reports()[0]['status'], 'cancelled')
        self.assertNotIn('headline', self.store.reports()[0]['body'])

    def test_completed_run_rejects_late_cancel_and_persists_report(self):
        r = self.research
        body = {**self.body(), 'beginner':self.body(), 'sources':[]}
        def execute(args, prompt, folder, events):
            (folder / 'report.json').write_text(json.dumps(body))
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(r, '_worker_auth'), patch.object(r, '_execute', side_effect=execute):
            r._run(self.ident, 'US.MU')
        self.assertEqual(self.store.reports()[0]['status'], 'complete')
        self.assertFalse(r.cancel(r.status['run_id'], 'US.MU')['accepted'])
        self.assertFalse(r.cancel_event.is_set())

    def test_cancel_marks_current_schedule_bucket_without_disabling_schedule(self):
        r = self.research
        self.store.save_settings({'analysis_enabled':True})
        feed = {'state':'connected','market_state':'MORNING','age_seconds':0,'symbol':'US.MU','quotes':{'US.MU':{'time':time.time()}}}
        now = datetime(2026, 10, 9, 10, 2, tzinfo=NEW_YORK)
        with patch.object(self.feed, 'snapshot', return_value=feed), patch('stork.research.datetime') as clock:
            clock.now.return_value = now
            r._failure(self.ident, ResearchCancelled())
        self.assertEqual(self.store.get('schedule_last_bucket'), schedule_bucket(self.store.settings(),feed,now))
        self.assertTrue(self.store.settings()['analysis_enabled'])
