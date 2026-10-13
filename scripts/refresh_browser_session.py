from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.settings import BROWSER_PROFILE_DIR
from app.resolver import _reject_retired_source


DEFAULT_URLS = [
    "https://www.douyin.com/",
    "https://www.xiaohongshu.com/",
]


def _target_url_from_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("url") if isinstance(payload, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else None
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--target-file")
    parser.add_argument("--state-file")
    args, _ = parser.parse_known_args()
    target_url = _target_url_from_file(args.target_file)
    urls = [target_url, *DEFAULT_URLS] if target_url else list(DEFAULT_URLS)
    urls = list(dict.fromkeys(url for url in urls if url))
    for url in urls:
        _reject_retired_source(url)

    print(f"Using project browser profile: {BROWSER_PROFILE_DIR}", flush=True)
    print("A real browser window will open with the project-isolated session profile.", flush=True)
    print("Complete any login / verification you need there, then close the browser window.", flush=True)

    with sync_playwright() as playwright:
        browser = None
        last_error: Exception | None = None
        base_options = {
            "user_data_dir": str(BROWSER_PROFILE_DIR),
            "headless": False,
            "viewport": {"width": 1360, "height": 920},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
            ],
        }

        for channel in ("msedge", "chrome", None):
            launch_options = dict(base_options)
            if channel:
                launch_options["channel"] = channel
            try:
                browser = playwright.chromium.launch_persistent_context(**launch_options)
                print(f"Opened browser session with channel: {channel or 'bundled-chromium'}", flush=True)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        if browser is None:
            raise RuntimeError(f"Unable to launch a browser session: {last_error}") from last_error

        try:
            existing_pages = list(browser.pages)
            if not existing_pages:
                existing_pages.append(browser.new_page())

            for index, url in enumerate(urls):
                page = existing_pages[index] if index < len(existing_pages) else browser.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    # Keep the browser open even if one tab fails to load.
                    pass

            browser.pages[0].bring_to_front()
            browser.pages[0].wait_for_event("close", timeout=0)
        finally:
            browser.close()
            if args.state_file:
                Path(args.state_file).unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
