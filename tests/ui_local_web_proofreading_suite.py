"""Browser-only regression for the local proofreading entry point."""
from tests.ui_smoke_suite import UISmokeTestCase
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import httpx


class LocalWebProofreading(UISmokeTestCase):
    def test_local_web_result_exposes_proofreading(self):
        self.page.goto(self.base_url + '/c/demovideo001', wait_until='domcontentloaded')
        self.page.get_by_role('button', name='校对', exact=True).click(timeout=10000)
        self.assertTrue(self.page.get_by_role('dialog', name='校对原文').is_visible())

    def test_result_details_are_compact_and_accessible(self):
        self.page.set_viewport_size({'width': 1280, 'height': 800})
        self.page.goto(self.base_url + '/c/demovideo001', wait_until='domcontentloaded')
        details = self.page.locator('.facts-strip')
        details.wait_for(state='visible')
        self.assertTrue(details.is_visible())
        self.assertLessEqual(details.bounding_box()['height'], 55)
        self.assertGreater(self.page.locator('.result-content-shell').bounding_box()['height'], 250)
        self.assertTrue(self.page.get_by_role('button', name='查看来源').is_visible())
        self.assertEqual(self.page.locator('.facts-strip .fact-chip-title').count(), 0)
        heading = self.page.locator('.deliverable-hero h1')
        self.assertEqual(heading.evaluate("el => getComputedStyle(el).overflowY"), 'visible')

    def test_summary_requires_manual_start(self):
        self.page.goto(self.base_url + '/c/demovideo001', wait_until='domcontentloaded')
        self.page.get_by_role('button', name='AI 摘要', exact=True).click(timeout=10000)
        dialog = self.page.get_by_role('dialog', name='摘要与要点')
        self.assertTrue(dialog.is_visible())
        self.assertTrue(dialog.get_by_role('button', name='生成摘要', exact=True).is_visible())

    def test_short_desktop_keeps_text_inside_scrollable_panel(self):
        self.page.set_viewport_size({'width': 1280, 'height': 700})
        self.page.goto(self.base_url + '/c/demovideo001', wait_until='domcontentloaded')
        shell = self.page.locator('.result-content-shell')
        shell.wait_for(state='visible')
        self.assertLess(shell.bounding_box()['height'], 500)
        self.assertEqual(shell.evaluate('el => getComputedStyle(el).overflowY'), 'auto')

    def test_summary_uses_mock_only_after_click_and_keeps_source(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(True)
                text = json.loads(data['messages'][1]['content'])['source']
                output = {'overview': '测试概述', 'ids': [0]} if '简短概述' in data['messages'][0]['content'] else {'points': [{'point': '测试要点', 'quote': text[:12]}]}
                body = json.dumps({'choices': [{'message': {'content': json.dumps(output, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with httpx.Client(trust_env=False) as client:
                response = client.patch(self.base_url + '/v1/local/preferences', json={'section': 'proofreading', 'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'test-key', 'model': 'fixture'})
                self.assertEqual(response.status_code, 200)
            self.page.goto(self.base_url + '/c/demovideo001', wait_until='domcontentloaded')
            self.page.get_by_role('button', name='AI 摘要', exact=True).click()
            dialog = self.page.get_by_role('dialog', name='摘要与要点')
            self.assertEqual(calls, [])
            dialog.get_by_role('button', name='生成摘要').click()
            dialog.get_by_text('测试要点').wait_for(timeout=20000)
            self.assertTrue(calls)
            dialog.get_by_text('查看原文依据', exact=True).click()
            self.assertTrue(dialog.locator('blockquote').last.is_visible())
        finally:
            server.shutdown(); server.server_close()
