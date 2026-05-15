from __future__ import annotations

from playwright.sync_api import sync_playwright

from app.settings import BROWSER_PROFILE_DIR, DEFAULT_USER_AGENT


DEFAULT_URLS = [
    "https://www.douyin.com/",
    "https://www.xiaohongshu.com/",
    "https://www.youtube.com/",
]


def main() -> int:
    print(f"Using project browser profile: {BROWSER_PROFILE_DIR}")
    print("A real browser window will open with the project-isolated session profile.")
    print("Complete any login / verification you need there, then close the browser window.")

    with sync_playwright() as playwright:
        browser = None
        last_error: Exception | None = None
        base_options = {
            "user_data_dir": str(BROWSER_PROFILE_DIR),
            "headless": False,
            "user_agent": DEFAULT_USER_AGENT,
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
                print(f"Opened browser session with channel: {channel or 'bundled-chromium'}")
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        if browser is None:
            raise RuntimeError(f"Unable to launch a browser session: {last_error}") from last_error

        try:
            existing_pages = list(browser.pages)
            if not existing_pages:
                existing_pages.append(browser.new_page())

            for index, url in enumerate(DEFAULT_URLS):
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
