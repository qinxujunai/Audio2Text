"""Real HTTP and browser integration; external model traffic stays on localhost."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import unittest

import httpx
from tests.ui_smoke_suite import UISmokeTestCase


class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        self.respond({"data": [{"id": "fixture-text-model"}, {"id": "fixture-other-model"}]})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert "禁止润色" in data["messages"][0]["content"]
        content = {"edits": [{"before": "错别宇", "after": "错别字", "reason": "明确错别字"},
                             {"before": "123元", "after": "128元", "reason": "应被拒绝"}]}
        self.respond({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]})


class LocalToolsUI(UISmokeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model_server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
        cls.model_url = f"http://127.0.0.1:{cls.model_server.server_port}/v1"
        threading.Thread(target=cls.model_server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.model_server.shutdown(); cls.model_server.server_close()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        for path in self.workspace_dir.glob("captures/*/capture.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["status"] in {"queued", "processing"}:
                data["status"] = "failed"
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.capture_path = self.workspace_dir / "captures/demovideo001/capture.json"
        data = json.loads(self.capture_path.read_text(encoding="utf-8"))
        data["result"]["primary_text"] = "这里有错别宇。金额123元。\n保留原话和全部内容。"
        data["result"]["views"]["primary"] = data["result"]["primary_text"]
        self.capture_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.original = self.capture_path.read_bytes()
        self.context.add_init_script("""window.__exports=[]; window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
          if(command==='desktop_runtime_info') return {api_base:location.origin,token:''};
          if(command==='save_text_export'){window.__exports.push(args);return {file_name:'fixture.txt'};}
          throw Error('Unexpected native command '+command);
        }};""")

    def configure(self):
        with httpx.Client(trust_env=False) as client:
            response = client.put(self.base_url + "/v1/local/preferences", json={"device": "cpu", "engine": "sensevoice",
                "base_url": self.model_url, "api_key": "fixture-local-key", "model": "fixture-text-model"})
            self.assertEqual(response.status_code, 200, response.text)

    def test_local_settings_detect_save_and_escape_all_sizes(self):
        errors = []
        self.page.on("pageerror", lambda error: errors.append(str(error)))
        for width, height in ((390,844), (760,680), (1280,820), (1920,1080)):
            self.page.set_viewport_size({"width":width,"height":height})
            self.page.goto(self.base_url, wait_until="domcontentloaded")
            self.page.locator('.hero-form textarea').fill('https://example.com/keyboard-check')
            self.page.evaluate("""() => {
                window.__settingsFormSubmits = 0;
                document.querySelector('.hero-form').addEventListener('submit', e => {
                    window.__settingsFormSubmits++; e.preventDefault(); e.stopPropagation();
                }, true);
            }""")
            button = self.page.get_by_role("button", name="本地设置", exact=True)
            self.assertEqual(button.inner_text(), "")
            self.assertEqual(button.evaluate("e => e.closest('.hero-form-tools') !== null"), True)
            self.assertEqual(button.evaluate("e => getComputedStyle(e).position === 'fixed'"), False)
            self.assertGreaterEqual(button.bounding_box()["width"], 44)
            self.page.get_by_role("button", name="本地设置", exact=True).click()
            dialog = self.page.get_by_role("dialog", name="本地设置")
            dialog.get_by_label("文字模型").fill("keyboard-check")
            dialog.get_by_label("文字模型").press("Enter")
            self.assertEqual(self.page.evaluate("window.__settingsFormSubmits"), 0)
            dialog.locator(".local-runtime-settings > summary").click()
            dialog.get_by_label("处理设备").select_option("cpu")
            dialog.get_by_role("button", name="保存转写设置").click()
            dialog.get_by_text("本地转写设置已保存", exact=False).wait_for()
            dialog.get_by_label("兼容 API 地址").fill(self.model_url)
            dialog.get_by_label("API 密钥").fill("fixture-local-key")
            dialog.get_by_role("button", name="检测模型").click()
            dialog.get_by_text("检测到 2 个模型", exact=False).wait_for()
            self.assertEqual(self.page.locator("#proof-models option").count(), 2)
            dialog.get_by_label("文字模型").fill("fixture-text-model")
            dialog.get_by_role("button", name="测试校对", exact=True).click()
            dialog.get_by_text("校对验证通过", exact=False).wait_for()
            dialog.get_by_role("button", name="保存校对设置").click()
            dialog.get_by_text("校对设置已保存", exact=False).wait_for()
            self.assertLessEqual(dialog.evaluate("e=>e.scrollWidth-e.clientWidth"), 1)
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
            for button in dialog.locator("button:visible").all():
                self.assertGreaterEqual(button.bounding_box()["height"], 44)
            destination = self.project_root / "tests_runtime/cuda-proofreading-verification"
            destination.mkdir(exist_ok=True, parents=True)
            self.page.screenshot(path=str(destination / f"settings-{width}.png"))
            self.page.keyboard.press("Escape")
            dialog.wait_for(state="detached")
            self.assertEqual(self.page.evaluate("document.activeElement?.getAttribute('aria-label')"), "本地设置")
        self.assertEqual(errors, [])

    def test_proofreading_full_http_flow_keeps_original_and_exports_both(self):
        self.configure()
        self.page.goto(self.base_url + "/c/demovideo001", wait_until="domcontentloaded")
        self.assertEqual(self.page.get_by_role("button", name="本地设置", exact=True).count(), 0)
        self.page.get_by_role("button", name="校对", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="校对原文")
        dialog.get_by_role("button", name="配置模型").click()
        settings = self.page.get_by_role("dialog", name="本地设置")
        settings.wait_for()
        self.page.keyboard.press("Escape"); settings.wait_for(state="detached")
        self.assertTrue(dialog.is_visible())
        dialog.get_by_role("button", name="开始校对").click()
        dialog.get_by_text("1 处候选修正", exact=False).wait_for()
        self.assertEqual(dialog.locator("input[type=checkbox]:checked").count(), 0)
        dialog.locator("input[type=checkbox]").check()
        dialog.get_by_role("button", name="保存所选修正", exact=False).click()
        dialog.get_by_text("校对版已单独保存", exact=False).wait_for()
        self.assertIn("金额123元", dialog.locator("pre").inner_text())
        self.assertIn("错别字", dialog.locator("pre").inner_text())
        dialog.get_by_role("button", name="下载原文", exact=True).click()
        self.page.wait_for_function("window.__exports.length===1")
        dialog.get_by_role("button", name="下载校对版", exact=True).click()
        self.page.wait_for_function("window.__exports.length===2")
        exports = self.page.evaluate("window.__exports")
        self.assertIn("错别宇", exports[0]["text"])
        self.assertIn("错别字", exports[1]["text"])
        self.assertEqual(self.capture_path.read_bytes(), self.original)
        self.page.screenshot(path=str(self.project_root / "tests_runtime/cuda-proofreading-verification/proofreading-desktop.png"))
        self.page.reload(wait_until="domcontentloaded")
        self.page.get_by_role("button", name="校对", exact=True).click()
        self.page.get_by_text("已保存校对版", exact=True).wait_for()

    def test_settings_pending_feedback_and_unsaved_runtime_are_explicit(self):
        self.configure()
        self.page.goto(self.base_url, wait_until="domcontentloaded")
        self.page.get_by_role("button", name="本地设置", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="本地设置")
        dialog.get_by_label("文字模型").wait_for()
        self.page.screenshot(path=str(self.project_root / "tests_runtime/product-upgrade/settings-refined.png"))
        self.page.evaluate("""() => {
            const original = window.fetch;
            window.fetch = async (...args) => {
                const response = await original(...args);
                if (String(args[0]).includes('/proofreading-check')) {
                    await new Promise(resolve => setTimeout(resolve, 1800));
                }
                return response;
            };
        }""")
        dialog.get_by_role("button", name="测试校对", exact=True).click()
        pending = dialog.get_by_role("button", name="正在测试…", exact=True)
        self.assertTrue(pending.is_disabled())
        self.assertEqual(dialog.locator('section[aria-labelledby="proof-settings-title"]').get_attribute("aria-busy"), "true")
        dialog.get_by_text("校对验证通过", exact=False).wait_for()
        dialog.locator(".local-runtime-settings > summary").click()
        dialog.get_by_label("处理设备").select_option("auto")
        dialog.get_by_text("先保存转写设置，再验证实际使用的设备。", exact=True).wait_for()
        self.assertTrue(dialog.get_by_role("button", name="验证 CUDA", exact=True).is_disabled())

    def test_late_poll_cannot_restore_running_state_after_cancel(self):
        self.page.goto(self.base_url + "/c/demovideo001", wait_until="domcontentloaded")
        self.page.evaluate("""() => {
            const original = window.fetch;
            window.__proofPolls = 0;
            window.__proofCancelled = false;
            window.__latePollReturned = false;
            window.fetch = async (...args) => {
                const url = String(args[0]);
                const json = data => new Response(JSON.stringify(data), {status: 200, headers: {'Content-Type': 'application/json'}});
                if (url.endsWith('/captures/demovideo001/proofreading/cancel')) {
                    window.__proofCancelled = true;
                    return json({status: 'cancelled'});
                }
                if (url.endsWith('/captures/demovideo001/proofreading') && (!args[1]?.method || args[1].method === 'GET')) {
                    const cancelled = window.__proofCancelled;
                    window.__proofPolls++;
                    if (window.__proofPolls === 2) {
                        await new Promise(resolve => setTimeout(resolve, 2500));
                        window.__latePollReturned = true;
                    }
                    return json({job: {id: 'race-test', status: cancelled ? 'cancelled' : 'running', progress: 25, edits: [], message: ''}, saved: null});
                }
                return original(...args);
            };
        }""")
        self.page.get_by_role("button", name="校对", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="校对原文")
        self.page.wait_for_function("window.__proofPolls === 2")
        dialog.get_by_role("button", name="取消校对", exact=True).click()
        dialog.get_by_text("校对已取消，原文未改变。", exact=True).wait_for()
        self.page.wait_for_function("window.__latePollReturned")
        self.assertTrue(dialog.get_by_text("校对已取消，原文未改变。", exact=True).is_visible())
        self.assertEqual(dialog.get_by_role("button", name="取消校对", exact=True).count(), 0)
        self.assertEqual(self.capture_path.read_bytes(), self.original)

    def test_slow_progress_response_is_not_starved_by_overlapping_polls(self):
        self.page.goto(self.base_url + "/c/demovideo001", wait_until="domcontentloaded")
        self.page.evaluate("""() => {
            const original = window.fetch;
            window.__slowProofPolls = 0;
            window.fetch = async (...args) => {
                if (String(args[0]).endsWith('/captures/demovideo001/proofreading')) {
                    const first = ++window.__slowProofPolls === 1;
                    if (!first) await new Promise(resolve => setTimeout(resolve, 2300));
                    return new Response(JSON.stringify({job: {id: 'slow-test', status: first ? 'running' : 'ready', progress: first ? 25 : 100, edits: [], message: ''}, saved: null}), {status: 200, headers: {'Content-Type': 'application/json'}});
                }
                return original(...args);
            };
        }""")
        self.page.get_by_role("button", name="校对", exact=True).click()
        dialog = self.page.get_by_role("dialog", name="校对原文")
        dialog.get_by_text("未发现符合保守规则的明确错误。", exact=True).wait_for(timeout=8000)
        self.assertEqual(self.page.evaluate("window.__slowProofPolls"), 2)


def load_tests(loader, tests, pattern):
    # Native bridge fixtures intentionally render buttons rather than web
    # download links. Run the web-only base suite separately, without this bridge.
    return unittest.TestSuite(loader.loadTestsFromName(name) for name in (
        __name__ + ".LocalToolsUI.test_local_settings_detect_save_and_escape_all_sizes",
        __name__ + ".LocalToolsUI.test_proofreading_full_http_flow_keeps_original_and_exports_both",
        __name__ + ".LocalToolsUI.test_settings_pending_feedback_and_unsaved_runtime_are_explicit",
        __name__ + ".LocalToolsUI.test_late_poll_cannot_restore_running_state_after_cancel",
        __name__ + ".LocalToolsUI.test_slow_progress_response_is_not_starved_by_overlapping_polls",
    ))


if __name__ == "__main__":
    unittest.main()
