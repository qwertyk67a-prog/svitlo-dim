"""Verified browser bootstrap is reused only within a single Python process."""
import asyncio
from power_config import provider_config
import provider_transport as transport


def test_browser_fallback_reused_for_address_and_schedule(monkeypatch):
    seen = {"browser": 0, "direct": 0, "cached": 0}

    async def browser(url):
        seen["browser"] += 1
        return {"_csrf-test": "fake"}, "csrf-test"

    async def request(config, form, *, cookies=None, csrf=None):
        if not cookies:
            seen["direct"] += 1
            raise RuntimeError("WAF challenge")
        seen["cached"] += 1
        return {"result": True, "method": form["method"]}

    monkeypatch.setattr(transport, "browser_cookies", browser)
    monkeypatch.setattr(transport, "_request", request)

    async def run():
        config = provider_config()
        one = await transport.ajax_post(config, {"method": "getHomeNum"})
        two = await transport.ajax_post(config, {"method": "checkDisconUpdate"})
        return one, two

    a, b = asyncio.run(run())
    assert a["method"] == "getHomeNum"
    assert b["method"] == "checkDisconUpdate"
    assert seen == {"browser": 1, "direct": 1, "cached": 2}


def test_rejected_cached_session_gets_fresh_browser(monkeypatch):
    seen = {"browser": 0}

    async def browser(url):
        seen["browser"] += 1
        return {"session": str(seen["browser"])}, "token"

    async def request(config, form, *, cookies=None, csrf=None):
        if not cookies:
            raise RuntimeError("No direct access")
        if cookies.get("session") == "1" and form["method"] == "checkDisconUpdate":
            raise RuntimeError("Session expired")
        return {"result": True}

    monkeypatch.setattr(transport, "browser_cookies", browser)
    monkeypatch.setattr(transport, "_request", request)

    async def run():
        config = provider_config()
        await transport.ajax_post(config, {"method": "getHomeNum"})
        await transport.ajax_post(config, {"method": "checkDisconUpdate"})

    asyncio.run(run())
    assert seen["browser"] == 2
