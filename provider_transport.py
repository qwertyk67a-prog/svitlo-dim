"""Compatible AJAX schedule transport with no baked-in network operator.

Format adapter: form-encoded Yii-style 'getHomeNum' and 'checkDisconUpdate'.
Different company protocols require a separate, tested adapter.
"""
import base64
import logging
import re
import secrets
from urllib.parse import unquote
from types import SimpleNamespace
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

LOG = logging.getLogger("power-transport")
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class DaySlots:
    def __init__(self, slots):
        self.slots = slots if isinstance(slots, dict) else {}


class WeekGroup:
    def __init__(self, days):
        self.days = {
            int(day): DaySlots(slots)
            for day, slots in days.items()
            if str(day).isdigit() and isinstance(slots, dict)
        }

    def get_day(self, day):
        return self.days.get(day)


def parse_preset(raw):
    if not isinstance(raw, dict):
        raise RuntimeError("Invalid provider preset")
    data = raw.get("data")
    labels = raw.get("time_zone")
    if not isinstance(data, dict) or not isinstance(labels, dict):
        raise RuntimeError("Incomplete provider preset")
    clean_labels = {}
    for key, value in labels.items():
        if isinstance(value, list):
            value = value[0] if value else ""
        clean_labels[str(key)] = str(value)
    groups = {
        group: WeekGroup(days)
        for group, days in data.items()
        if isinstance(group, str) and isinstance(days, dict)
    }
    return SimpleNamespace(
        groups=groups, time_zone=clean_labels,
        is_active=bool(groups and clean_labels),
    )


def parse_fact(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeError("Invalid provider daily fact")
    raw_days = raw.get("data")
    if not isinstance(raw_days, dict):
        raise RuntimeError("Incomplete provider daily fact")
    days = {}
    for stamp, group_map in raw_days.items():
        if isinstance(group_map, dict):
            days[str(stamp)] = {
                str(group): {
                    str(slot): str(status)
                    for slot, status in slots.items()
                }
                for group, slots in group_map.items()
                if isinstance(slots, dict)
            }
    return SimpleNamespace(days=days, update=raw.get("update"))


def parse_homes(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), (dict, list)):
        raise RuntimeError("Invalid provider address directory")
    data = raw["data"]
    if isinstance(data, list):
        data = {
            str(row.get("num") or row.get("house_num") or row.get("name")): row
            for row in data if isinstance(row, dict)
            and (row.get("num") or row.get("house_num") or row.get("name"))
        }
    homes = {}
    for number, values in data.items():
        records = ([values] if isinstance(values, dict)
                   else [x for x in values if isinstance(x, dict)]
                   if isinstance(values, list) else [])
        if not records:
            continue
        groups = []
        for record in records:
            entries = record.get("sub_type_reason")
            if isinstance(entries, list):
                for group in entries:
                    if isinstance(group, str) and group not in groups:
                        groups.append(group)
        primary = next(
            (row for row in records if row.get("sub_type") or row.get("start_date")),
            records[0],
        )
        homes[str(number)] = SimpleNamespace(
            group_ids=groups,
            sub_type=str(primary.get("sub_type") or ""),
            start_date=str(primary.get("start_date") or ""),
            end_date=str(primary.get("end_date") or ""),
            outage_type=str(primary.get("type") or ""),
            house_number=str(number),
        )
    return SimpleNamespace(houses=homes)


async def browser_cookies(site_url):
    """Optional anti-bot fallback, using the already installed browser tools."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    async with Stealth().use_async(async_playwright()) as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=AGENT)
            page = await context.new_page()
            await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_selector(
                    'meta[name="csrf-token"]',
                    state="attached", timeout=30000,
                )
            except Exception:
                pass
            csrf = await page.get_attribute('meta[name="csrf-token"]', "content")
            cookies = {c["name"]: c["value"] for c in await context.cookies()}
            return cookies, csrf
        finally:
            await browser.close()


def form_data(method, fields=None, update_fact=None):
    result = {"method": method}
    for idx, (name, value) in enumerate(fields or []):
        result[f"data[{idx}][name]"] = name
        result[f"data[{idx}][value]"] = value
    if update_fact is not None:
        idx = len(fields or [])
        result[f"data[{idx}][name]"] = "updateFact"
        result[f"data[{idx}][value]"] = update_fact
    return result


class ProviderRateLimit(RuntimeError):
    """Respect explicit source rate-limits without browser retries."""


_CSRF_RE = re.compile(r'i:1;s:\d+:"([^"]+)"')


def _csrf_token_from_cookies(*sources):
    """Read only signed Yii-formatted CSRF cookies; never log token values."""
    for source in sources:
        if not source:
            continue
        try:
            entries = source.items()
        except AttributeError:
            continue
        for name, value in entries:
            if not str(name).startswith("_csrf"):
                continue
            match = _CSRF_RE.search(unquote(str(value)))
            if match:
                return match.group(1)
    return None


def _mask_csrf(raw):
    payload = raw.encode("utf-8")
    mask = secrets.token_bytes(len(payload))
    return base64.urlsafe_b64encode(
        mask + bytes(a ^ b for a, b in zip(mask, payload, strict=True))
    ).decode("ascii")


def _checked_response(response):
    status = response.status_code
    if status == 429:
        raise ProviderRateLimit("Official source rate limited requests")
    if status != 200:
        raise RuntimeError(f"Provider API HTTP {status}")
    try:
        raw = response.json()
    except (ValueError, TypeError):
        raise RuntimeError("Provider API returned invalid JSON") from None
    if not isinstance(raw, dict) or raw.get("result") is False:
        raise RuntimeError("Provider API returned invalid data")
    return raw


async def _request(config, form, *, cookies=None, csrf=None):
    """Bootstrap the signed Yii CSRF cookie before sending the real request.

    A 400 response to an initial tokenless probe is expected. The session
    retains the cookie, whose embedded raw token must be masked in the header.
    """
    headers = {
        "User-Agent": AGENT,
        "Origin": urlparse(config["site_url"]).scheme + "://" +
                  urlparse(config["site_url"]).netloc,
        "Referer": config["site_url"],
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with AsyncSession(
        timeout=20, cookies=cookies or {}, headers=headers,
        impersonate="chrome120",
    ) as session:
        token = csrf
        if not token:
            raw_token = _csrf_token_from_cookies(session.cookies, cookies)
            if not raw_token:
                probe = await session.post(
                    config["ajax_url"], data={"method": "getHomeNum"},
                )
                if probe.status_code == 429:
                    raise ProviderRateLimit("Official source rate limited requests")
                raw_token = _csrf_token_from_cookies(
                    probe.cookies, session.cookies
                )
            if raw_token:
                token = _mask_csrf(raw_token)
            else:
                raise RuntimeError("Official source did not provide a CSRF token")
        response = await session.post(
            config["ajax_url"], data=form,
            headers={**headers, "X-CSRF-Token": token},
        )
        if response.status_code == 400:
            # A rejected or expired token can be refreshed once.
            probe = await session.post(
                config["ajax_url"], data={"method": "getHomeNum"},
            )
            if probe.status_code == 429:
                raise ProviderRateLimit("Official source rate limited requests")
            new_raw = _csrf_token_from_cookies(probe.cookies, session.cookies)
            if not new_raw:
                raise RuntimeError("Official source rejected CSRF refresh")
            response = await session.post(
                config["ajax_url"], data=form,
                headers={**headers, "X-CSRF-Token": _mask_csrf(new_raw)},
            )
        return _checked_response(response)


async def ajax_post(config, form):
    try:
        return await _request(config, form)
    except ProviderRateLimit:
        raise
    except Exception as exc:
        LOG.warning("Direct provider request failed (%s), using browser",
                    type(exc).__name__)
    cookies, csrf = await browser_cookies(config["site_url"])
    try:
        return await _request(config, form, cookies=cookies, csrf=csrf)
    except ProviderRateLimit:
        raise
    except Exception:
        raise RuntimeError("Official provider request unavailable") from None


async def get_homes(config, city, street):
    raw = await ajax_post(config, form_data(
        "getHomeNum",
        [("city", city), ("street", street)],
        update_fact="01.01.2000 00:00",
    ))
    return parse_homes(raw)


async def get_schedule(config):
    raw = await ajax_post(config, {
        "method": "checkDisconUpdate",
        "update": "01.01.2000 00:00",
    })
    return SimpleNamespace(
        preset=parse_preset(raw.get("preset")),
        fact=parse_fact(raw.get("fact")),
    )
