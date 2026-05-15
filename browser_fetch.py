"""
browser_fetch.py — Playwright-based renderer for JS-heavy Yandex Maps pages.

Yandex Maps is a React SPA: photos, promos, and some features are loaded
dynamically and are not present in the static HTML fetched by requests.
This module launches a real Chromium instance to get the fully rendered page.

Used by org_collector for photo extraction. Not used for bulk search enrichment
(too slow: ~4s per page vs ~0.5s for requests).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Candidate Chromium executables in order of priority
def _find_chromium() -> str:
    candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        # This sandbox
        "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        # Playwright default cache (Linux)
        *[str(p) for p in sorted(
            Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
            reverse=True,
        )],
        # Playwright default cache (macOS)
        *[str(p) for p in sorted(
            Path.home().glob(
                "Library/Caches/ms-playwright/chromium-*/"
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium"
            ),
            reverse=True,
        )],
        # System chromium
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return ""


def fetch_rendered(
    url: str,
    wait_for_selector: str = "",
    scroll_px: int = 800,
    timeout_ms: int = 20_000,
    extra_wait_ms: int = 2_000,
) -> str:
    """
    Navigate to *url* with a real Chromium instance and return the fully
    rendered HTML after JS execution and optional scroll.

    Returns empty string when Playwright is not installed or the browser fails.

    Parameters
    ----------
    url            : Target URL.
    wait_for_selector : CSS selector to wait for before capturing (optional).
    scroll_px      : How many pixels to scroll down to trigger lazy loaders.
    timeout_ms     : Navigation timeout.
    extra_wait_ms  : Additional wait after scroll for lazy content to appear.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.debug("Playwright not installed — skipping browser fetch")
        return ""

    chromium_path = _find_chromium()
    launch_kwargs: dict = {"headless": True}
    if chromium_path:
        launch_kwargs["executable_path"] = chromium_path

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch_kwargs)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="ru-RU",
                ignore_https_errors=True,
                extra_http_headers={
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                },
            )
            page = ctx.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PWTimeout:
                log.warning("browser_fetch: page load timeout for %s", url)
                browser.close()
                return ""

            # Scroll to trigger lazy-loaded photo carousels
            if scroll_px:
                page.evaluate(f"window.scrollBy(0, {scroll_px})")

            # Wait for specific selector (e.g. photo thumbnails)
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=5_000)
                except PWTimeout:
                    pass  # selector may not exist; proceed anyway

            # Give React a moment to finish rendering after scroll
            if extra_wait_ms:
                page.wait_for_timeout(extra_wait_ms)

            html = page.content()
            browser.close()

            # Sanity check: real pages are large; tiny responses are error pages
            # (e.g. "Host not in allowlist", captcha-only pages, etc.)
            if len(html) < 5_000:
                log.debug("browser_fetch: response too small (%d bytes) for %s", len(html), url)
                return ""

            log.debug("browser_fetch: got %d bytes for %s", len(html), url)
            return html

    except Exception as exc:
        log.warning("browser_fetch failed for %s: %s", url, exc)
        return ""
