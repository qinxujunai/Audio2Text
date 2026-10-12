"""Install the exact accepted candidate on a fresh GitHub Windows runner.

Never run against the user's profile: a fresh CI profile and owned temporary
installation directory are required before any installer is executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from contextlib import contextmanager, ExitStack

import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def assert_clean_runner(environment: dict[str, str], data_root: Path) -> Path:
    if environment.get("GITHUB_ACTIONS") != "true" or environment.get("RUNNER_OS") != "Windows":
        raise RuntimeError("Fresh Windows CI runner required; local user profile is not a test target.")
    temporary = Path(environment.get("RUNNER_TEMP", "")).resolve()
    if not environment.get("RUNNER_TEMP") or not temporary.is_dir():
        raise RuntimeError("Owned CI temporary directory is missing.")
    if data_root.exists():
        raise RuntimeError("Application data already exists; this is not a clean first-run acceptance.")
    return temporary


@contextmanager
def ci_debug_policy(environment: dict[str, str], data_root: Path):
    """Temporary application-only HKLM override for elevated hosted runners.

    WebView2 ignores env/HKCU flags for elevated hosts. Never put debug flags
    into the distributed app or change policy on the user's machine.
    """
    assert_clean_runner(environment, data_root)
    import winreg
    path = r"SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY) as key:
        try:
            previous = winreg.QueryValueEx(key, "wanxiang-chengwen.exe")
        except FileNotFoundError:
            previous = None
        try:
            winreg.SetValueEx(key, "wanxiang-chengwen.exe", 0, winreg.REG_SZ, "--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1")
            yield
        finally:
            if previous is None:
                winreg.DeleteValue(key, "wanxiang-chengwen.exe")
            else:
                winreg.SetValueEx(key, "wanxiang-chengwen.exe", 0, previous[1], previous[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data_root = Path(os.environ["LOCALAPPDATA"]) / "online.praxisai.wanxiang/workspace"
    temporary = assert_clean_runner(dict(os.environ), data_root)
    install = temporary / "wanxiang-clean-install"
    if install.exists():
        raise RuntimeError("CI installation target already exists; refusing to overlay it.")
    with args.installer.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != args.sha256.lower():
        raise RuntimeError("Candidate installer digest mismatch.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"installer_sha256": digest, "clean_profile_before_install": True, "checks": {}, "passed": False}
    process = None
    native_log = None
    overrides = ExitStack()
    stage = "install"
    try:
        overrides.enter_context(ci_debug_policy(dict(os.environ), data_root))
        report["checks"]["ci_only_elevated_debug_override"] = True
        subprocess.run([str(args.installer.resolve()), "/S", f"/D={install}"], check=True, timeout=180)
        app = install / "wanxiang-chengwen.exe"
        assert app.is_file()
        report["checks"]["installed_main_sha256"] = hashlib.file_digest(app.open("rb"), "sha256").hexdigest()
        environment = dict(os.environ)
        environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=9222 --remote-debugging-address=127.0.0.1"
        native_log = (args.output.parent / "clean-native-process.log").open("w", encoding="utf-8")
        process = subprocess.Popen([str(app)], env=environment, stdout=native_log, stderr=native_log, creationflags=subprocess.CREATE_NO_WINDOW)
        stage = "native_first_run"
        with sync_playwright() as playwright:
            deadline = time.monotonic() + 60
            while True:
                try:
                    browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=2000)
                    break
                except Exception as connection_error:
                    if process.poll() is not None or time.monotonic() > deadline:
                        report["checks"]["native_exit_code"] = process.poll()
                        report["checks"]["native_wait_seconds"] = 60
                        report["checks"]["webview_processes"] = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", "@(Get-Process -Name msedgewebview2 -ErrorAction SilentlyContinue).Count"], text=True).strip()
                        report["checks"]["webview_runtime_folders"] = [str(p) for p in (Path(os.environ["PROGRAMFILES(X86)"]) / "Microsoft/EdgeWebView/Application").glob("*/msedgewebview2.exe")]
                        report["checks"]["cdp_error_type"] = type(connection_error).__name__
                        # Only a fresh CI profile is allowed here. This error
                        # contains the fixed CDP endpoint, not application data.
                        report["checks"]["cdp_error"] = str(connection_error)[:1200]
                        report["checks"]["debug_flag_process_count"] = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", "@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedgewebview2.exe' -and $_.CommandLine -match 'remote-debugging-port=9222' }).Count"], text=True).strip()
                        report["checks"]["webview_arguments"] = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'msedgewebview2.exe' } | ForEach-Object { [regex]::Matches($_.CommandLine, '--(?:remote-debugging|webview-exe-name|user-data-dir|embedded-browser-webview)[^ ]*').Value }"], text=True).splitlines()
                        report["checks"]["application_process_count"] = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", "@(Get-Process -Name wanxiang-chengwen -ErrorAction SilentlyContinue).Count"], text=True).strip()
                        try:
                            with httpx.Client(trust_env=False, timeout=3) as diagnostic:
                                response = diagnostic.get("http://127.0.0.1:9222/json/version")
                                report["checks"]["cdp_http_status"] = response.status_code
                        except httpx.HTTPError as network_error:
                            report["checks"]["cdp_network_error"] = type(network_error).__name__
                        raise RuntimeError("Installed native window did not start.")
                    time.sleep(.25)
            # CDP can be ready while the first target is still about:blank.
            deadline = time.monotonic() + 30
            page = None
            while page is None:
                page = next((p for c in browser.contexts for p in c.pages if "tauri.localhost" in p.url), None)
                if page is None:
                    if process.poll() is not None or time.monotonic() > deadline:
                        from urllib.parse import urlsplit
                        report["checks"]["native_page_origins"] = [urlsplit(p.url).scheme + "://" + urlsplit(p.url).netloc for c in browser.contexts for p in c.pages]
                        with httpx.Client(trust_env=False, timeout=3) as diagnostic:
                            targets = diagnostic.get("http://127.0.0.1:9222/json/list").json()
                        report["checks"]["native_debug_targets"] = [{"type": t.get("type"), "origin": urlsplit(t.get("url", "")).scheme + "://" + urlsplit(t.get("url", "")).netloc} for t in targets]
                        raise RuntimeError("Native application page did not become ready.")
                    # Property polling plus time.sleep never pumps the sync
                    # Playwright transport. Wait on SDK events so the newly
                    # created WebView target reaches the local page cache.
                    try:
                        pages = [p for c in browser.contexts for p in c.pages]
                        if pages:
                            pages[0].wait_for_url("**://tauri.localhost/**", timeout=1000)
                        elif browser.contexts:
                            browser.contexts[0].wait_for_event("page", timeout=1000)
                    except PlaywrightTimeoutError:
                        pass
            page.set_default_timeout(30000)
            errors = []
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.get_by_role("button", name="安装并继续", exact=True).wait_for()
            page.screenshot(path=str(args.output.parent / "clean-first-run.png"))
            stage = "first_component_download"
            page.get_by_role("button", name="安装并继续", exact=True).click()
            page.locator(".hero-form").wait_for(timeout=420000)
            runtime = page.evaluate("() => window.__TAURI_INTERNALS__.invoke('desktop_runtime_info')")
            with httpx.Client(base_url=runtime["api_base"], headers={"X-Wanxiang-Desktop-Token": runtime["token"]}, timeout=60, trust_env=False) as client:
                response = client.get("/v1/runtime/packs"); response.raise_for_status()
                packs = response.json()["packs"]
                assert packs and all(p.get("installed") for p in packs if p.get("required"))
                report["checks"]["first_components"] = [{"id": p["id"], "installed": p["installed"]} for p in packs]
                stage = "cpu_transcription"
                root = Path(__file__).resolve().parents[1]
                ffmpeg = data_root / "runtime/ffmpeg/ffmpeg.exe"
                video = temporary / "公开安装验收示例.mp4"
                subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-stream_loop", "-1", "-i", str(root / "tests/fixtures/ui-preview.mp4"), "-i", str(root / "tests/fixtures/asr-smoke.wav"), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(video)], check=True, timeout=60)
                page.locator('input[type="file"]').set_input_files(str(video))
                page.get_by_role("button", name="处理这个文件", exact=True).click()
                page.locator(".deliverable-workspace").wait_for(timeout=240000)
                text = page.locator(".result-prose").inner_text()
                assert len(text.strip()) > 5
                health = client.get("/health"); health.raise_for_status()
                assert health.json()["transcription_runtime"]["actual_device"] == "cpu"
                expected_version = json.loads((root / "web/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
                assert health.json()["version"] == expected_version
                report["checks"]["cpu_transcription"] = {"actual_device": "cpu", "text_chars": len(text), "version": health.json()["version"]}
                stage = "playback_and_downloads"
                page.locator("video").evaluate("v => {v.muted=true;return v.play()}")
                page.wait_for_timeout(1200)
                playback = page.locator("video").evaluate("v => ({time:v.currentTime,error:v.error?.code||null,frames:v.getVideoPlaybackQuality().totalVideoFrames})")
                assert playback["time"] > 0 and playback["frames"] > 0 and playback["error"] is None
                page.locator("video").evaluate("v => v.pause()")
                # Invoke the same registered export commands used by the UI;
                # verify files really arrived in the native Downloads folder.
                captures = client.get("/v1/captures"); captures.raise_for_status()
                capture_id = captures.json()["items"][0]["id"]
                saved = []
                for artifact_type in ("txt", "source_audio", "source_media"):
                    saved.append(page.evaluate("args => window.__TAURI_INTERNALS__.invoke('save_artifact',args)", {"captureId": capture_id, "artifactType": artifact_type}))
                downloads = Path(os.environ["USERPROFILE"]) / "Downloads/万象成文"
                assert all((downloads / item["file_name"]).is_file() and (downloads / item["file_name"]).stat().st_size > 0 for item in saved)
                report["checks"]["playback"] = playback
                report["checks"]["native_downloads"] = [item["file_name"] for item in saved]
                page.screenshot(path=str(args.output.parent / "clean-cpu-result.png"))
                assert not errors
                report["passed"] = True
            browser.close()
    except Exception as error:
        report.update(failed_stage=stage, error_type=type(error).__name__)
    finally:
        if process and process.poll() is None:
            process.terminate(); process.wait(timeout=20)
        if native_log:
            native_log.close()
        overrides.close()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Clean Windows acceptance: {'passed' if report['passed'] else 'failed at '+stage}; installer={digest}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
