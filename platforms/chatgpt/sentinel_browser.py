"""Browser-backed Sentinel token acquisition for create_account flow."""

from __future__ import annotations

import json
from typing import Any, Optional

from core.proxy_utils import build_playwright_proxy_config


SENTINEL_FRAME_URL = (
    "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6"
)


def _iter_session_cookies(session) -> list[dict[str, Any]]:
    cookies = []
    jar = getattr(session, "cookies", None)
    if not jar:
        return cookies

    for cookie in getattr(jar, "jar", []):
        try:
            same_site = getattr(cookie, "_rest", {}).get("SameSite")
            if same_site:
                same_site = str(same_site).capitalize()
                if same_site not in {"Lax", "None", "Strict"}:
                    same_site = None

            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path or "/",
                    "secure": bool(cookie.secure),
                    "httpOnly": False,
                    **({"sameSite": same_site} if same_site else {}),
                }
            )
        except Exception:
            continue
    return cookies


def get_browser_sentinel_token(
    *,
    session,
    device_id: str,
    flow: str,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None,
    accept_language: Optional[str] = None,
    referer: Optional[str] = None,
    timeout_ms: int = 25000,
) -> Optional[str]:
    """Use Playwright to execute SentinelSDK in-browser and return token JSON."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    cookies = _iter_session_cookies(session)
    launch_opts: dict[str, Any] = {"headless": True}
    context_opts: dict[str, Any] = {}

    if proxy:
        proxy_cfg = build_playwright_proxy_config(proxy)
        if proxy_cfg:
            launch_opts["proxy"] = proxy_cfg

    if user_agent:
        context_opts["user_agent"] = user_agent
    if accept_language:
        context_opts["locale"] = accept_language.split(",", 1)[0].strip() or "en-US"
        context_opts["extra_http_headers"] = {"Accept-Language": accept_language}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_opts)
        try:
            context = browser.new_context(**context_opts)
            if cookies:
                context.add_cookies(cookies)

            page = context.new_page()
            page.goto(
                SENTINEL_FRAME_URL, wait_until="domcontentloaded", timeout=timeout_ms
            )

            if referer:
                page.evaluate(
                    """
                    (value) => {
                      try {
                        Object.defineProperty(document, 'referrer', {
                          configurable: true,
                          get: () => value,
                        })
                      } catch (_) {}
                    }
                    """,
                    referer,
                )

            result = page.evaluate(
                """
                async ({ deviceId, flow }) => {
                  const sdk = window.SentinelSDK
                  if (!sdk || typeof sdk.init !== 'function' || typeof sdk.token !== 'function') {
                    throw new Error('SentinelSDK not ready')
                  }

                  await sdk.init(flow)
                  const token = await sdk.token(flow)
                  if (!token) {
                    throw new Error('Sentinel token empty')
                  }

                  let parsed = token
                  if (typeof token === 'string') {
                    parsed = JSON.parse(token)
                  }
                  parsed.id = parsed.id || deviceId
                  parsed.flow = parsed.flow || flow
                  return parsed
                }
                """,
                {"deviceId": device_id, "flow": flow},
            )

            if not isinstance(result, dict):
                return None
            result.setdefault("id", device_id)
            result.setdefault("flow", flow)
            if not result.get("c"):
                return None
            if result.get("t") in (None, ""):
                return None
            return json.dumps(result, separators=(",", ":"))
        except PlaywrightTimeoutError:
            return None
        finally:
            browser.close()
