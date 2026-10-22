from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx

from app import browser_provider as provider


def state_html(state: dict) -> str:
    return '<script>window.__INITIAL_STATE__=' + json.dumps(state) + ';</script>'


def mobile_html(**changes) -> str:
    note = {
        'noteId': 'synthetic-note', 'title': '示例标题', 'desc': '第一段\n第二段',
        'type': 'normal', 'imageList': [{'urlDefault': 'http://example.com/image.jpg'}],
    }
    note.update(changes)
    return state_html({'noteData': {'data': {'noteData': note}}})


class XiaohongshuMobileTests(unittest.TestCase):
    def test_mobile_note_uses_shared_text_and_image_normalization(self):
        payload = provider._extract_xiaohongshu_note_payload(mobile_html())
        self.assertIsNotNone(payload)
        self.assertEqual(payload['note_id'], 'synthetic-note')
        self.assertEqual(payload['desc'], '第一段\n第二段')
        self.assertEqual(payload['image_urls'], ['https://example.com/image.jpg'])

    def test_mobile_video_and_live_photo_keep_existing_media_rules(self):
        html = mobile_html(
            type='video',
            video={'media': {'stream': {'h264': [{'masterUrl': 'http://example.com/main.mp4'}]}}},
            imageList=[{'urlDefault': 'http://example.com/cover.jpg',
                        'livePhotoUrl': 'http://example.com/live.mp4'}],
        )
        payload = provider._extract_xiaohongshu_note_payload(html)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['video_url'], 'https://example.com/main.mp4')
        self.assertEqual(payload['live_photo_video_urls'], ['https://example.com/live.mp4'])

    def test_desktop_note_still_takes_precedence(self):
        state = {'note': {'noteDetailMap': {'pc': {'note': {'desc': 'desktop'}}}},
                 'noteData': {'data': {'noteData': {'noteId': 'h5', 'desc': 'mobile'}}}}
        payload = provider._extract_xiaohongshu_note_payload(state_html(state))
        self.assertEqual(payload['note_id'], 'pc')
        self.assertEqual(payload['desc'], 'desktop')

    def test_missing_or_malformed_mobile_data_is_not_a_note(self):
        for node in [None, [], 'invalid', {}, {'data': None}, {'data': []},
                     {'data': {'noteData': []}}, {'data': {'noteData': {'noteId': 'empty'}}},
                     {'data': {'noteData': {'title': '登录'}}}]:
            with self.subTest(node=node):
                self.assertIsNone(provider._extract_xiaohongshu_note_payload(
                    state_html({'noteData': node})))

    def fetch(self, url, responder):
        calls = []
        def send(client, request, **kwargs):
            requested_url = str(request.url)
            calls.append((requested_url, client.headers['User-Agent']))
            return httpx.Response(200, text=responder(calls[-1][1]),
                                  request=httpx.Request('GET', requested_url))
        with patch.object(provider, '_browser_needs_relay', return_value=False), \
             patch.object(provider, '_browser_proxy_for_platform', return_value=''), \
             patch.object(httpx.Client, 'send', new=send):
            result = provider._fetch_xiaohongshu_html(url)
        return result, calls

    def test_shared_mobile_url_reads_note_without_opening_browser(self):
        url = 'https://www.xiaohongshu.com/discovery/item/synthetic?xsec_token=synthetic&xsec_source=pc_share'
        def respond(agent):
            return mobile_html() if 'iPhone' in agent else '<title>登录</title>'
        with patch.object(provider, '_launch_context') as browser, \
             patch.object(provider, '_fetch_xiaohongshu_relay_fallback') as relay:
            # Exercise the public media adapter, not just the state helper.
            def send(client, request, **kwargs):
                requested_url = str(request.url)
                return httpx.Response(200, text=respond(client.headers['User-Agent']),
                                      request=httpx.Request('GET', requested_url))
            with patch.object(provider, '_browser_needs_relay', return_value=False), \
                 patch.object(provider, '_browser_proxy_for_platform', return_value=''), \
                 patch.object(httpx.Client, 'send', new=send):
                result = provider.fetch_xiaohongshu_page(url)
        self.assertEqual(result.body_text, '第一段\n第二段')
        self.assertEqual(result.image_urls, ['https://example.com/image.jpg'])
        browser.assert_not_called()
        relay.assert_not_called()

    def test_desktop_shell_falls_back_once_preserving_complete_share_url(self):
        url = 'https://www.xiaohongshu.com/explore/synthetic?xsec_token=synthetic&xsec_source=pc_share'
        result, calls = self.fetch(url, lambda agent: mobile_html() if 'iPhone' in agent else '<title>登录</title>')
        self.assertIsNotNone(provider._extract_xiaohongshu_note_payload(result[2]))
        self.assertEqual([call[0] for call in calls], [url, url])
        self.assertNotIn('iPhone', calls[0][1])
        self.assertIn('iPhone', calls[1][1])

    def test_mobile_share_tries_mobile_first_and_stops_on_success(self):
        _, calls = self.fetch('https://www.xiaohongshu.com/discovery/item/synthetic', lambda agent: mobile_html())
        self.assertEqual(len(calls), 1)
        self.assertIn('iPhone', calls[0][1])

    def test_empty_shell_has_at_most_two_attempts(self):
        _, calls = self.fetch('https://www.xiaohongshu.com/explore/synthetic', lambda agent: '<title>登录</title>')
        self.assertEqual(len(calls), 2)

    def test_desktop_success_needs_only_one_request(self):
        html = state_html({'note': {'noteDetailMap': {'pc': {'note': {'desc': '正文'}}}}})
        _, calls = self.fetch('https://www.xiaohongshu.com/explore/synthetic', lambda agent: html)
        self.assertEqual(len(calls), 1)

    def test_second_request_failure_preserves_primary_response(self):
        shell = '<title>原始提示</title>'
        def send(client, request, **kwargs):
            url = str(request.url)
            if 'iPhone' in client.headers['User-Agent']:
                raise httpx.ReadTimeout('synthetic timeout')
            return httpx.Response(200, text=shell, request=httpx.Request('GET', url))
        with patch.object(provider, '_browser_needs_relay', return_value=False), \
             patch.object(provider, '_browser_proxy_for_platform', return_value=''), \
             patch.object(httpx.Client, 'send', new=send):
            result = provider._fetch_xiaohongshu_html('https://www.xiaohongshu.com/explore/synthetic')
        self.assertEqual(result[2], shell)

    def test_elapsed_budget_does_not_start_another_attempt(self):
        with patch.object(provider.time, 'monotonic', side_effect=[0, 0, 0, 0, 61]):
            _, calls = self.fetch('https://www.xiaohongshu.com/explore/synthetic', lambda agent: '<title>登录</title>')
        self.assertEqual(len(calls), 1)

    def test_existing_proxy_and_tls_verification_are_preserved(self):
        with patch.object(provider, '_browser_needs_relay', return_value=False), \
             patch.object(provider, '_browser_proxy_for_platform', return_value='http://localhost:1234'), \
             patch.object(httpx, 'Client') as client:
            response = httpx.Response(200, text=mobile_html(), request=httpx.Request('GET', 'https://example.com'))
            client.return_value.__enter__.return_value.stream.return_value.__enter__.return_value = response
            provider._fetch_xiaohongshu_html('https://www.xiaohongshu.com/discovery/item/synthetic')
        kwargs = client.call_args.kwargs
        self.assertEqual(kwargs['proxy'], 'http://localhost:1234')
        self.assertFalse(kwargs['trust_env'])
        self.assertNotIn('verify', kwargs)
        self.assertLessEqual(kwargs['timeout'], provider.DEFAULT_TIMEOUT_SECONDS)

    def test_slow_stream_cannot_return_success_after_budget(self):
        body = mobile_html().encode()
        class SlowResponse(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    for offset in range(0, len(body), 10):
                        self.wfile.write(body[offset:offset+10]); self.wfile.flush()
                        time.sleep(.03)
                except OSError:
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowResponse)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.object(provider, 'DEFAULT_TIMEOUT_SECONDS', .1), \
                 patch.object(provider, '_browser_needs_relay', return_value=False), \
                 patch.object(provider, '_browser_proxy_for_platform', return_value=''):
                with self.assertRaises(provider.BrowserProviderError):
                    provider._fetch_xiaohongshu_html(f'http://127.0.0.1:{server.server_port}/note')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_http_denial_keeps_existing_fast_structured_failure(self):
        calls=[]
        def send(client, request, **kwargs):
            calls.append(request)
            return httpx.Response(403, request=request)
        with patch.object(provider, '_browser_needs_relay', return_value=False), \
             patch.object(provider, '_browser_proxy_for_platform', return_value=''), \
             patch.object(httpx.Client, 'send', new=send):
            with self.assertRaises(provider.BrowserProviderError):
                provider._fetch_xiaohongshu_html('https://www.xiaohongshu.com/explore/synthetic')
        self.assertEqual(len(calls), 1)

    def test_oversized_response_is_rejected(self):
        class Oversized(httpx.SyncByteStream):
            def __iter__(self):
                for _ in range(9):
                    yield b'x' * (1024 * 1024)
        def send(client, request, **kwargs):
            return httpx.Response(200, request=request, stream=Oversized())
        with patch.object(provider, '_browser_needs_relay', return_value=False), \
             patch.object(provider, '_browser_proxy_for_platform', return_value=''), \
             patch.object(httpx.Client, 'send', new=send):
            with self.assertRaises(provider.BrowserProviderError) as raised:
                provider._fetch_xiaohongshu_html('https://www.xiaohongshu.com/explore/synthetic')
        self.assertEqual(raised.exception.reason_code, 'browser_request_failed')
        self.assertFalse(raised.exception.retryable)


if __name__ == '__main__':
    unittest.main()
