import unittest
from unittest.mock import patch
from app import extractors


class YouTubeProxyTests(unittest.TestCase):
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
