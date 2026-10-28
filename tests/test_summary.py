import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app.proofreading import config_identity


class SummaryTests(unittest.TestCase):
    def test_selection_ids_reject_unknown_and_duplicates(self):
        from app.summary import validate_selection
        self.assertEqual(validate_selection({'ids': [2, 0]}, 3), [2, 0])
        for invalid in ({'ids': [4]}, {'ids': [1, 1]}, {'ids': []}, {'ids': [True]}):
            with self.assertRaises(ValueError):
                validate_selection(invalid, 3)

    def test_overview_requires_valid_source_references(self):
        from app.summary import validate_overview
        self.assertEqual(validate_overview({'overview': '一段概述', 'ids': [1]}, 2), ('一段概述', [1]))
        for invalid in ({'overview': '内容', 'ids': [9]}, {'overview': '', 'ids': [0]}, {'overview': '内容', 'ids': []}):
            with self.assertRaises(ValueError):
                validate_overview(invalid, 2)

    def test_short_or_whitespace_tail_is_merged_without_losing_text(self):
        from app.summary import summary_chunks
        for tail in ('甲', '甲乙', '甲乙丙', '   '):
            source = '原' * 3000 + tail
            pieces = summary_chunks(source)
            self.assertEqual(''.join(piece for _, piece in pieces), source)
            self.assertTrue(all(len(piece.strip()) >= 20 for _, piece in pieces))

    def test_start_rejects_configuration_changed_since_preview(self):
        from app.summary import SummaryService
        with tempfile.TemporaryDirectory() as root:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='这是一段需要摘要的文字。'))
            service = SummaryService(SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root)))
            with patch('app.summary.proofreading_config', return_value={'model': 'other', 'api_key': 'test'}):
                with self.assertRaisesRegex(ValueError, '设置已变化'):
                    service.start('cap', 'stale-preview')
            service.executor.shutdown()

    def test_summary_routes_use_local_boundary_and_start_only_on_post(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import local_tools_api
        app = FastAPI(); app.include_router(local_tools_api.router)
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/local/captures/cap/summary', headers={'Origin': 'https://evil.example'}).status_code, 403)
            with patch.object(local_tools_api, 'summary_service', create=True) as service:
                service.status.return_value = {'job': None, 'saved': None}
                self.assertEqual(client.get('/v1/local/captures/cap/summary').status_code, 200)
                service.start.assert_not_called()
                service.start.return_value = {'id': 'test', 'status': 'running'}
                self.assertEqual(client.post('/v1/local/captures/cap/summary', json={'config_id': 'a' * 64}).status_code, 200)
                service.start.assert_called_once_with('cap', 'a' * 64)

    def test_ai_settings_cannot_change_during_summary(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import local_tools_api
        app = FastAPI(); app.include_router(local_tools_api.router)
        with TestClient(app) as client, patch.object(local_tools_api.summary_service, 'jobs', {'cap': {'status': 'running'}}):
            response = client.patch('/v1/local/preferences', json={'section': 'proofreading', 'model': 'other'})
            self.assertEqual(response.status_code, 409)

    def test_quotes_must_come_from_original(self):
        from app.summary import validate_points
        points = validate_points('甲说今天下雨。乙说明天晴。', [
            {'point': '甲说下雨', 'quote': '今天下雨'},
            {'point': '不存在的内容', 'quote': '从未出现'},
        ])
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['quote'], '今天下雨')

    def test_cancel_prevents_late_result_from_being_saved(self):
        from app.summary import SummaryService
        entered, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as root:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='这是一段需要摘要的文字。'))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root))
            service = SummaryService(repo)
            def request(*args):
                entered.set(); release.wait(3)
                return [{'point': '内容摘要', 'quote': '需要摘要的文字'}]
            with patch('app.summary.proofreading_config', return_value={'model': 'fixture', 'api_key': 'test'}), patch('app.summary.request_points', side_effect=request):
                job = service.start('cap', config_identity({'model': 'fixture', 'api_key': 'test'}))
                self.assertTrue(entered.wait(2))
                service.cancel('cap', job['id'])
                release.set(); service.executor.shutdown()
            self.assertEqual(service.status('cap')['job']['status'], 'cancelled')
            self.assertIsNone(service.status('cap')['saved'])
            self.assertEqual(capture.result.primary_text, '这是一段需要摘要的文字。')

    def test_success_persists_separately_and_checks_source_hash(self):
        from app.summary import SummaryService
        with tempfile.TemporaryDirectory() as root:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='这是测试原文。关键内容值得记录。'))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root))
            service = SummaryService(repo)
            with patch('app.summary.proofreading_config', return_value={'model': 'fixture', 'api_key': 'test'}), patch('app.summary.request_points', return_value=[{'point': '记录关键内容', 'quote': '关键内容值得记录'}]), patch('app.summary.request_overview', return_value=('内容概述', [0])):
                service.start('cap', config_identity({'model': 'fixture', 'api_key': 'test'})); service.executor.shutdown(wait=True)
            self.assertEqual(service.status('cap')['job']['status'], 'ready')
            self.assertEqual(service.saved('cap')['points'][0]['quote'], '关键内容值得记录')
            self.assertEqual(capture.result.primary_text, '这是测试原文。关键内容值得记录。')
            capture.result.primary_text = '原文已更改。'
            self.assertIsNone(service.saved('cap'))

    def test_failed_chunk_never_saves_partial_summary(self):
        from app.summary import SummaryService
        with tempfile.TemporaryDirectory() as root:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='第一部分文字。' * 500))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root))
            service = SummaryService(repo)
            with patch('app.summary.proofreading_config', return_value={'model': 'fixture', 'api_key': 'test'}), patch('app.summary.request_points', side_effect=[[{'point': '第一部分', 'quote': '第一部分文字'}], ValueError('failure')]):
                service.start('cap', config_identity({'model': 'fixture', 'api_key': 'test'})); service.executor.shutdown(wait=True)
            self.assertEqual(service.status('cap')['job']['status'], 'failed')
            self.assertIsNone(service.saved('cap'))

    def test_cancel_after_last_chunk_skips_merge_request(self):
        from app.summary import SummaryService
        with tempfile.TemporaryDirectory() as root:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='第一部分文字。' * 1500))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root))
            service = SummaryService(repo)
            entered, release = threading.Event(), threading.Event()
            count = [0]
            def request(*_):
                count[0] += 1
                if count[0] == 4:
                    entered.set(); release.wait(3)
                return [{'point': '要点', 'quote': '第一部分文字'}] * 3
            config = {'model': 'fixture', 'api_key': 'test'}
            with patch('app.summary.proofreading_config', return_value=config), patch('app.summary.request_points', side_effect=request), patch('app.summary.request_selection') as merge:
                job = service.start('cap', config_identity(config))
                self.assertTrue(entered.wait(2))
                service.cancel('cap', job['id']); release.set(); service.executor.shutdown(wait=True)
                merge.assert_not_called()

    def test_long_source_selects_verified_candidates_by_id(self):
        from app.summary import SummaryService
        with tempfile.TemporaryDirectory() as root:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='第一部分文字。' * 1500))
            service = SummaryService(SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root)))
            config = {'model': 'fixture', 'api_key': 'test'}
            with patch('app.summary.proofreading_config', return_value=config), patch('app.summary.request_points', return_value=[{'point': '要点', 'quote': '第一部分文字'}] * 3), patch('app.summary.request_selection', return_value=[0, 4, 8]) as merge, patch('app.summary.request_overview', return_value=('内容概述', [0])):
                service.start('cap', config_identity(config)); service.executor.shutdown(wait=True)
                self.assertEqual(service.status('cap')['job']['status'], 'ready')
                self.assertEqual(len(service.saved('cap')['points']), 3)
                self.assertEqual(merge.call_count, 1)

    def test_video_sized_source_is_processed_in_multiple_segments(self):
        from app.summary import SummaryService
        with tempfile.TemporaryDirectory() as root:
            text = '这是一段可以核对的访谈原文。' * 2500
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text=text))
            service = SummaryService(SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(root)))
            config = {'model': 'fixture', 'api_key': 'test'}
            with patch('app.summary.proofreading_config', return_value=config), patch('app.summary.request_points', return_value=[{'point': '访谈要点', 'quote': '可以核对的访谈原文'}]) as segments, patch('app.summary.request_selection', side_effect=lambda points, _: list(range(min(len(points), 8)))), patch('app.summary.request_overview', return_value=('访谈概述', [0])):
                service.start('cap', config_identity(config)); service.executor.shutdown(wait=True)
                saved = service.saved('cap')
                self.assertEqual(service.status('cap')['job']['status'], 'ready')
                self.assertGreater(segments.call_count, 8)
                self.assertEqual(saved['overview'], '访谈概述')
                self.assertTrue(all(text[p['start']:p['end']] == p['quote'] for p in saved['points']))
                self.assertEqual(capture.result.primary_text, text)
