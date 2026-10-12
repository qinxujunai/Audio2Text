import unittest
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from app import extractors
from scripts import ytdlp_media_worker


class YouTubeProxyTests(unittest.TestCase):
    def test_media_proxy_crosses_real_worker_serialization_boundary(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(extractors.subprocess, 'Popen') as popen:
            process = popen.return_value
            process.communicate.return_value = ('{"ok":true,"info":{"id":"sample"}}', '')
            process.returncode = 0
            extractors._run_ytdlp_media_download(url='https://www.youtube.com/watch?v=doSR9Mq_taw', capture_dir=Path(directory), options={'format':'best', 'outtmpl':str(Path(directory)/'%(id)s.%(ext)s'), 'proxy':'http://127.0.0.1:7890'}, browser_cookie_source=None, timeout_seconds=5)
            request = json.loads(process.communicate.call_args.args[0])
            self.assertEqual(request['proxy'], 'http://127.0.0.1:7890')
            request['user_agent'] = 'fixture'
            with patch.object(ytdlp_media_worker, 'YoutubeDL') as downloader, patch.object(ytdlp_media_worker.sys, 'stdin', io.StringIO(json.dumps(request))), patch.object(ytdlp_media_worker.sys, 'stdout', io.StringIO()):
                downloader.return_value.__enter__.return_value.extract_info.return_value = {'id':'sample'}
                self.assertEqual(ytdlp_media_worker.main(), 0)
                self.assertEqual(downloader.call_args.args[0]['proxy'], 'http://127.0.0.1:7890')

    def test_subtitle_download_retains_platform_routing(self):
        with tempfile.TemporaryDirectory() as directory:
            def download(url, target, **kwargs):
                self.assertEqual(kwargs.get('platform'), 'youtube')
                target.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nExample\n', encoding='utf-8')
            with patch.object(extractors, '_download_file', side_effect=download):
                text, _ = extractors._download_subtitle({'url':'https://www.youtube.com/api/timedtext','ext':'vtt'},Path(directory),platform='youtube')
                self.assertIn('Example', text)
    def test_local_youtube_uses_system_proxy_without_affecting_china_platforms(self):
        with patch.object(extractors, 'DEPLOYMENT_MODE', 'local'), patch.object(extractors, 'CN_PLATFORM_PROXY', ''), patch('urllib.request.getproxies', return_value={'https': 'http://127.0.0.1:7890'}):
            self.assertEqual(extractors._proxy_for_platform('youtube'), 'http://127.0.0.1:7890')
            self.assertIsNone(extractors._proxy_for_platform('bilibili'))
            self.assertIsNone(extractors._proxy_for_platform(None))

    def test_cloud_does_not_inherit_system_proxy_or_change_china_override(self):
        with patch.object(extractors, 'DEPLOYMENT_MODE', 'cloud_preview'), patch.object(extractors, 'CN_PLATFORM_PROXY', 'http://cn-proxy.example:8080'), patch('urllib.request.getproxies', return_value={'https': 'http://unexpected.example:7890'}):
            self.assertIsNone(extractors._proxy_for_platform('youtube'))
            self.assertEqual(extractors._proxy_for_platform('bilibili'), 'http://cn-proxy.example:8080')

    def test_transcript_session_keeps_netrc_and_environment_auth_disabled(self):
        with patch.object(extractors, '_proxy_for_platform', return_value='http://127.0.0.1:7890'):
            session = extractors._requests_session_without_env(platform='youtube')
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies['https'], 'http://127.0.0.1:7890')
        session.close()
