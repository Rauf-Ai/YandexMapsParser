"""
browser_fetch.py — Playwright-based renderer for JS-heavy Yandex Maps pages.

Yandex Maps is a React SPA: photos, promos, and service listings are loaded
dynamically and absent in the static HTML fetched by requests.Session.

Two usage modes:
  1. fetch_rendered()   — one-shot, launches/closes a browser per call.
                          Used by org_collector for deep ZIP collection.
  2. BrowserSession     — reusable session; launch once, fetch N pages fast.
                          Used by yandex_parser.collect() for bulk enrichment.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_CONTEXT_OPTS: dict = dict(
    user_agent=_UA,
    locale="ru-RU",
    ignore_https_errors=True,
    extra_http_headers={
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    },
)


def _find_chromium() -> str:
    candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        *[str(p) for p in sorted(
            Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
            reverse=True,
        )],
        *[str(p) for p in sorted(
            Path.home().glob(
                "Library/Caches/ms-playwright/chromium-*/"
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium"
            ),
            reverse=True,
        )],
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return ""


def _launch_kwargs() -> dict:
    kwargs: dict = {"headless": True}
    path = _find_chromium()
    if path:
        kwargs["executable_path"] = path
    return kwargs


def _is_real_page(html: str) -> bool:
    return len(html) >= 5_000


# ---------------------------------------------------------------------------
# One-shot helper (used by org_collector)
# ---------------------------------------------------------------------------
def fetch_rendered(
    url: str,
    wait_for_selector: str = "",
    scroll_px: int = 800,
    timeout_ms: int = 20_000,
    extra_wait_ms: int = 2_000,
) -> str:
    """Launch Chromium, fetch *url*, return fully rendered HTML or ''."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.debug("Playwright not installed")
        return ""

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**_launch_kwargs())
            ctx = browser.new_context(**_CONTEXT_OPTS)
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except PWTimeout:
                log.warning("browser_fetch timeout: %s", url)
                browser.close()
                return ""

            if scroll_px:
                page.evaluate(f"window.scrollBy(0, {scroll_px})")
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=5_000)
                except PWTimeout:
                    pass
            if extra_wait_ms:
                page.wait_for_timeout(extra_wait_ms)

            html = page.content()
            browser.close()
            if not _is_real_page(html):
                log.debug("browser_fetch: tiny response (%d B) for %s", len(html), url)
                return ""
            log.debug("browser_fetch: %d B for %s", len(html), url)
            return html
    except Exception as exc:
        log.warning("browser_fetch failed for %s: %s", url, exc)
        return ""


# ---------------------------------------------------------------------------
# Reusable session (used by yandex_parser.collect for bulk enrichment)
# ---------------------------------------------------------------------------
class BrowserSession:
    """
    A single Chromium browser kept alive across many page fetches.

    Launch once at the start of a collect() call, reuse for every company
    that needs browser enrichment, then stop(). This avoids the ~2s
    browser-launch overhead on every company.

    Usage::

        with BrowserSession() as bs:
            html = bs.fetch("https://yandex.ru/maps/org/123/")
    """

    def __init__(self):
        self._pw = None
        self._browser = None
        self.available = False

    def start(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().__enter__()
            self._browser = self._pw.chromium.launch(**_launch_kwargs())
            self.available = True
            log.debug("BrowserSession started")
            return True
        except Exception as exc:
            log.debug("BrowserSession unavailable: %s", exc)
            self.available = False
            return False

    def stop(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.__exit__(None, None, None)
        except Exception:
            pass
        self.available = False
        self._browser = None
        self._pw = None

    def fetch(
        self,
        url: str,
        scroll_px: int = 400,
        wait_ms: int = 1_500,
        timeout_ms: int = 15_000,
    ) -> str:
        """Fetch *url* in a fresh context, return HTML or ''."""
        if not self.available or not self._browser:
            return ""
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
            ctx = self._browser.new_context(**_CONTEXT_OPTS)
            page = ctx.new_page()
            loaded = False
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                loaded = True
            except PWTimeout:
                # Page may be partially loaded — try to get content anyway
                try:
                    _ = page.url  # check page is still alive
                    loaded = page.url not in ("", "about:blank")
                except Exception:
                    pass
            if not loaded:
                ctx.close()
                return ""
            if scroll_px:
                try:
                    page.evaluate(f"window.scrollBy(0, {scroll_px})")
                except Exception:
                    pass
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            try:
                html = page.content()
            except Exception:
                ctx.close()
                return ""
            ctx.close()
            if not _is_real_page(html):
                return ""
            return html
        except Exception as exc:
            log.debug("BrowserSession.fetch %s: %s", url, exc)
            return ""

    # Context manager support
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
