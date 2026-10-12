"""Verify the installed WebView UI via localhost CDP; never print desktop tokens."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--output", type=Path, default=Path("tests_runtime/cuda-proofreading-verification/installed-native-ui.json"))
    parser.add_argument("--capture-id", help="Existing local result to verify; otherwise choose the longest local completed transcript.")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(os.environ["LOCALAPPDATA"]) / "online.praxisai.wanxiang/workspace"
    capture_id = args.capture_id
    if capture_id is None:
        records = [json.loads(path.read_text(encoding="utf-8")) for path in (workspace / "captures").glob("*/capture.json")]
        completed = [record for record in records if record.get("status") == "done" and (record.get("result") or {}).get("primary_text")]
        if not completed:
            raise RuntimeError("An existing completed local result is required for native history acceptance.")
        capture_id = max(completed, key=lambda record: len(record["result"]["primary_text"]))["id"]
    if not capture_id or not all(character.isalnum() or character in "_-" for character in capture_id):
        raise ValueError("Invalid local capture identifier.")
    capture_path = workspace / f"captures/{capture_id}/capture.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    before = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    report = {"capture_id": capture_id, "checks": {}, "console_errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        page = next(p for c in browser.contexts for p in c.pages if "tauri.localhost" in p.url)
        page.set_default_timeout(20000)
        page.on("pageerror", lambda error: report["console_errors"].append(str(error)))
        responses = {}
        def response_received(response):
            if response.url.endswith(("/v1/local/preferences", "/v1/local/cuda-check")) and response.status == 200:
                data = response.json()
                responses[response.url.rsplit("/", 1)[-1]] = data
        page.on("response", response_received)
        page.get_by_role("button", name="本地设置", exact=True).click()
        dialog = page.get_by_role("dialog", name="本地设置")
        dialog.locator(".local-runtime-settings > summary").click()
        dialog.get_by_label("识别模型").wait_for()
        assert dialog.get_by_label("识别模型").input_value() == "sensevoice"
        assert dialog.get_by_label("处理设备").input_value() == "cuda"
        dialog.get_by_role("button", name="验证 CUDA", exact=True).click()
        dialog.get_by_text("真实推理验证通过", exact=False).wait_for()
        assert responses["cuda-check"]["device"] == "cuda"
        # Initial preferences can precede warm-up; the completed inference is authoritative.
        report["checks"]["actual_cuda"] = responses["cuda-check"]
        page.screenshot(path=str(args.output.parent / "installed-settings-cuda.png"))
        page.keyboard.press("Escape"); dialog.wait_for(state="detached")
        more = page.get_by_role("button", name="查看更多", exact=True)
        if more.count(): more.click()
        page.locator(".recent-inline-card").filter(has_text=capture["title"]).first.click()
        page.locator(".deliverable-workspace").wait_for()
        page.screenshot(path=str(args.output.parent / "installed-long-result.png"))
        page.get_by_role("button", name="校对", exact=True).click()
        dialog = page.get_by_role("dialog", name="校对原文")
        dialog.get_by_role("button", name="下载原文", exact=True).click()
        dialog.get_by_text("原文已保存到", exact=False).wait_for()
        page.screenshot(path=str(args.output.parent / "installed-proofreading-dialog.png"))
        page.keyboard.press("Escape"); dialog.wait_for(state="detached")
        assert hashlib.sha256(capture_path.read_bytes()).hexdigest() == before
        report["checks"].update(installed_dialog="passed", original_download="passed", original_capture_unchanged=True)
        assert not report["console_errors"]
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # Disconnect only; do not close the user's native application.
        browser.close()
    print("Installed native CUDA setting, real inference, long result, proofreading dialog and native original download: passed.")


if __name__ == "__main__":
    main()
