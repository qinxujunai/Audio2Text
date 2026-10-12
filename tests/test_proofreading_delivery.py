"""Delivery regressions for real-provider protocols and isolated local settings."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import local_preferences as prefs, local_tools_api as api, proofreading as proof


class ProofreadingDeliveryTests(unittest.TestCase):
    config = {'base_url': 'https://api.deepseek.com', 'api_key': 'secret-fixture-key', 'model': 'deepseek-flash'}

    def setUp(self):
        self.config_patch = patch.object(proof, 'proofreading_config', return_value=dict(self.config))
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def request(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.object(proof.httpx, 'Client', return_value=client):
            return proof.request_edits('错别宇。金额三百元。', dict(self.config))

    def test_deepseek_disables_thinking_and_requests_json(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(body['thinking'], {'type': 'disabled'})
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertFalse(body['stream'])
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"edits":[]}'}}]})
        self.assertEqual(self.request(handle), [])

    def test_truncation_is_distinct_from_invalid_json(self):
        with self.assertRaises(proof.ProofreadingError) as error:
            self.request(lambda r: httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{"edits":['}}]}))
        self.assertEqual(error.exception.code, 'output_truncated')

    def test_empty_response_does_not_report_configuration_failure(self):
        with self.assertRaises(proof.ProofreadingError) as error:
            self.request(lambda r: httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': None}}]}))
        self.assertEqual(error.exception.code, 'empty_output')

    def test_bad_json_is_rejected_without_exposing_provider_output(self):
        with self.assertRaises(proof.ProofreadingError) as error:
            self.request(lambda r: httpx.Response(200, json={'choices': [{'message': {'content': 'secret-fixture-key'}}]}))
        self.assertEqual(error.exception.code, 'invalid_output')
        self.assertNotIn('secret-fixture-key', str(error.exception))

    def test_auth_balance_limit_model_and_server_errors_are_distinct(self):
        for status, provider_code, expected in ((401,'invalid_api_key','unauthorized'), (402,'','insufficient_balance'),
                (429,'','rate_limited'), (404,'model_not_found','model_unavailable'), (503,'','service_unavailable')):
            with self.subTest(status=status):
                with self.assertRaises(proof.ProofreadingError) as error:
                    self.request(lambda r: httpx.Response(status, json={'error': {'code': provider_code, 'message': 'secret-fixture-key'}}))
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn('secret-fixture-key', str(error.exception))

    def test_timeout_has_actionable_code(self):
        def timeout(r): raise httpx.ReadTimeout('secret-fixture-key', request=r)
        with self.assertRaises(proof.ProofreadingError) as error:
            self.request(timeout)
        self.assertEqual(error.exception.code, 'timeout')

    def test_ai_patch_preserves_cuda_and_its_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(prefs, 'WORKSPACE_DIR', Path(directory)):
            original = {'device': 'cuda', 'engine': 'sensevoice', 'model_path': 'missing-but-existing-preference', 'sdk_path': 'old-sdk'}
            prefs.atomic_json(Path(directory)/'local-preferences.json', {'transcription': original})
            app = FastAPI(); app.include_router(api.router)
            with TestClient(app) as client:
                response = client.patch('/v1/local/preferences', json={'section': 'proofreading', 'base_url': 'https://api.deepseek.com', 'api_key': 'secret-fixture-key', 'model': 'deepseek-flash'})
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(prefs.read_preferences()['transcription'], original)
            self.assertNotIn('secret-fixture-key', response.text)

    def test_validation_uses_unsaved_configuration_and_never_saves(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(prefs, 'WORKSPACE_DIR', Path(directory)), patch.object(api, 'verify_proofreading', return_value={'status': 'ready', 'model': 'new', 'elapsed_ms': 10}) as verify:
            app = FastAPI(); app.include_router(api.router)
            with TestClient(app) as client:
                r = client.post('/v1/local/proofreading-check', json={'base_url': 'https://api.deepseek.com', 'api_key': 'secret-fixture-key', 'model': 'new'})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(verify.call_args.args[0]['model'], 'new')
            self.assertFalse((Path(directory)/'local-preferences.json').exists())

    def test_invalid_local_request_never_echoes_secret_input(self):
        app = FastAPI(); app.include_router(api.router)
        with TestClient(app) as client:
            r = client.post('/v1/local/proofreading-check', json={'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'api_key': {'secret': 'private-fixture'}})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn('private-fixture', r.text)

    def test_changed_model_cannot_restore_previous_ready_suggestions(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='错别宇。'))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(directory))
            first = proof.ProofreadingService(repo)
            with patch.object(proof, 'proofreading_config', return_value=dict(self.config)), patch.object(proof, 'request_edits', return_value=[{'before': '错别宇', 'after': '错别字'}]):
                first.start('cap'); first.executor.shutdown(wait=True)
            second = proof.ProofreadingService(repo)
            with patch.object(proof, 'proofreading_config', return_value={**self.config, 'model': 'other'}):
                self.assertIsNone(second.status('cap')['job'])
            second.executor.shutdown(wait=True)

    def test_resume_skips_completed_chunks_and_retains_original(self):
        with tempfile.TemporaryDirectory() as directory:
            text = '第一段错别宇。' + '甲'*2593 + '第二段错别宇。'
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text=text))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(directory))
            config = dict(self.config)
            calls = []
            def fail_second(chunk, _):
                calls.append(chunk)
                if len(calls) == 2: raise RuntimeError('provider unavailable')
                return [{'before': '错别宇', 'after': '错别字'}]
            first = proof.ProofreadingService(repo)
            with patch.object(proof, 'proofreading_config', return_value=config), patch.object(proof, 'request_edits', side_effect=fail_second):
                first.start('cap'); first.executor.shutdown(wait=True)
            self.assertEqual(first.status('cap')['job']['status'], 'failed')
            second = proof.ProofreadingService(repo)
            self.assertEqual(second.status('cap')['job']['completed_chunks'], 1)
            with patch.object(proof, 'proofreading_config', return_value=config), patch.object(proof, 'request_edits', return_value=[{'before': '错别宇', 'after': '错别字'}]) as request:
                second.start('cap'); second.executor.shutdown(wait=True)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(second.status('cap')['job']['status'], 'ready')
            self.assertEqual(len(second.status('cap')['job']['edits']), 2)
            self.assertEqual(capture.result.primary_text, text)

    def test_changed_credentials_and_source_invalidate_in_memory_job(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='错别宇。'))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(directory))
            service = proof.ProofreadingService(repo)
            with patch.object(proof, 'request_edits', return_value=[]):
                service.start('cap'); service.executor.shutdown(wait=True)
            with patch.object(proof, 'proofreading_config', return_value={**self.config, 'credential_revision': 'changed'}):
                self.assertIsNone(service.status('cap')['job'])
                service.cancel('cap')  # A late cancellation after invalidation is harmless.
            capture.result.primary_text = '新原文。'
            self.assertIsNone(service.status('cap')['job'])

    def test_stale_acceptance_cannot_select_a_new_jobs_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='错别宇。机其人。'))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(directory))
            service = proof.ProofreadingService(repo)
            with patch.object(proof, 'request_edits', return_value=[{'before': '错别宇', 'after': '错别字'}]):
                old = service.start('cap')
                while service.jobs['cap']['status'] == 'running': threading.Event().wait(.01)
            with patch.object(proof, 'request_edits', return_value=[{'before': '机其人', 'after': '机器人'}]):
                new = service.start('cap'); service.executor.shutdown(wait=True)
            with self.assertRaises(proof.StaleProofreadingError):
                service.accept('cap', [0], job_id=old['id'])
            self.assertFalse((Path(directory)/'proofreading.json').exists())
            self.assertEqual(service.accept('cap', [0], job_id=new['id'])['text'], '错别宇。机器人。')

    def test_cancelled_late_response_cannot_overwrite_replacement_job(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = SimpleNamespace(status='done', result=SimpleNamespace(primary_text='错别宇。'))
            repo = SimpleNamespace(get=lambda _: capture, capture_workspace=lambda _: Path(directory))
            service = proof.ProofreadingService(repo); entered = threading.Event(); release = threading.Event()
            def late(*args): entered.set(); release.wait(3); return []
            with patch.object(proof, 'proofreading_config', return_value=dict(self.config)), patch.object(proof, 'request_edits', side_effect=late):
                old = service.start('cap'); self.assertTrue(entered.wait(2)); service.cancel('cap')
                new = service.start('cap'); self.assertNotEqual(old['id'], new['id']); release.set(); service.executor.shutdown(wait=True)
            self.assertEqual(service.status('cap')['job']['id'], new['id'])
            self.assertEqual(service.status('cap')['job']['status'], 'ready')


if __name__ == '__main__': unittest.main()
