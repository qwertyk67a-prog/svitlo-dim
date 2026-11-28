"""Compatible AJAX schedule transport with no baked-in network operator.

Format adapter: form-encoded Yii-style 'getHomeNum' and 'checkDisconUpdate'.
An installation can also select field-mapped JSON feeds.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from contextlib import contextmanager
from time import monotonic

import base64
import logging
import re
import secrets
import unicodedata
from urllib.parse import unquote
from types import SimpleNamespace
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

LOG = logging.getLogger("power-transport")
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


@contextmanager
def provider_timing(operation):
    started = monotonic()
    try:
        yield
    finally:
        LOG.info("Provider timing %s %.0f ms", operation, (monotonic() - started) * 1000)


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
    stamps_by_date = {}
    kyiv = ZoneInfo("Europe/Kyiv")
    for stamp, group_map in raw_days.items():
        if not isinstance(group_map, dict):
            raise RuntimeError("Invalid provider daily groups")
        try:
            day = datetime.fromtimestamp(int(stamp), kyiv).date()
        except (TypeError, ValueError, OverflowError, OSError):
            # once() reports a malformed timestamp while processing valid days.
            key = str(stamp)
        else:
            key = stamps_by_date.setdefault(day, str(stamp))
        groups = days.setdefault(key, {})
        for group, slots in group_map.items():
            values = ({str(slot): str(status) for slot, status in slots.items()}
                      if isinstance(slots, dict) else {})
            group = str(group)
            # Conflicting records for one local day cannot prove cancellation.
            if group in groups and groups[group] != values:
                groups[group] = {}
            else:
                groups[group] = values
    return SimpleNamespace(days=days, update=raw.get("update"))


def catalog_queue(value, config):
    if not config or not config.get("queue_catalog") or value in config["queue_catalog"]:
        return value
    def spelling(text):
        text = unicodedata.normalize("NFKC", text).casefold()
        text = re.sub(r"[\s\u200b\ufeff]+", "", text)
        return re.sub(r"^(?:підчерга|черга|група)(?:no|[:№])?", "", text)

    label = spelling(value)
    prefix = spelling(config.get("queue_display_prefix", ""))
    matches = [group for group in config["queue_catalog"]
               if spelling(group) == label
               or (re.fullmatch(r"[0-9]+\.[0-9]+", label)
                   and spelling(group).removeprefix(prefix) == label)]
    return matches[0] if len(matches) == 1 else value


def select_house_event(records):
    if len(records) == 1:
        return records[0]
    from alerts import exact_provider_time
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    def priority(row):
        start = exact_provider_time(row.get("start_date"))
        end = exact_provider_time(row.get("end_date"))
        # Prefer a current window, then the nearest future event, then incomplete or ended events.
        if start and start <= now and (not end or now < end):
            return 0, -start.timestamp()
        if start and start > now and (not end or end >= start):
            return 1, start.timestamp()
        if end and end <= now:
            return 3, -end.timestamp()
        return 2, 0
    events = [row for row in records if any(row.get(k) for k in ("start_date", "end_date", "sub_type"))]
    return min(events, key=lambda row: (*priority(row), *(str(row.get(k) or "") for k in
               ("start_date", "end_date", "sub_type", "type"))), default={})


def parse_homes(raw, config=None):
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), (dict, list)):
        raise RuntimeError("Invalid provider address directory")
    data = raw["data"]
    if isinstance(data, list):
        by_number = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            number = row.get("num") or row.get("house_num") or row.get("name")
            if number:
                by_number.setdefault(str(number), []).append(row)
        data = by_number
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
            if isinstance(entries, str):
                entries = [entries]
            if isinstance(entries, list):
                for group in entries:
                    if isinstance(group, str):
                        group = catalog_queue(group, config)
                        if group not in groups:
                            groups.append(group)
        primary = select_house_event(records)
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
        timeout=20, cookies=cookies or {}, headers=headers, allow_redirects=False,
        impersonate="chrome120",
    ) as session:
        token = csrf
        if not token:
            raw_token = _csrf_token_from_cookies(session.cookies, cookies)
            if not raw_token:
                with provider_timing("csrf"):
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
        with provider_timing(form["method"]):
            response = await session.post(
                config["ajax_url"], data=form,
                headers={**headers, "X-CSRF-Token": token},
            )
        if response.status_code == 400:
            # A rejected or expired token can be refreshed once.
            with provider_timing("csrf"):
                probe = await session.post(
                    config["ajax_url"], data={"method": "getHomeNum"},
                )
            if probe.status_code == 429:
                raise ProviderRateLimit("Official source rate limited requests")
            new_raw = _csrf_token_from_cookies(probe.cookies, session.cookies)
            if not new_raw:
                raise RuntimeError("Official source rejected CSRF refresh")
            with provider_timing(form["method"]):
                response = await session.post(
                    config["ajax_url"], data=form,
                    headers={**headers, "X-CSRF-Token": _mask_csrf(new_raw)},
                )
        return _checked_response(response)


_BROWSER_SESSION_CACHE = {}
_BROWSER_SESSION_TTL = 120  # Per-process only; never persist WAF cookies in Git.


async def ajax_post(config, form):
    """Reuse verified browser cookies for the second query in the same run.

    A scheduled GitHub job starts a new process. The cache therefore never
    shares credentials between runs, properties or repositories.
    """
    import time

    key = (config["site_url"], config["ajax_url"])
    cached = _BROWSER_SESSION_CACHE.get(key)
    if cached is not None:
        expires, cookies, csrf = cached
        if time.monotonic() < expires:
            try:
                return await _request(
                    config, form, cookies=dict(cookies), csrf=csrf
                )
            except ProviderRateLimit:
                raise
            except Exception as exc:
                LOG.warning("Cached session rejected (%s), refreshing",
                            type(exc).__name__)
        _BROWSER_SESSION_CACHE.pop(key, None)

    try:
        return await _request(config, form)
    except ProviderRateLimit:
        raise
    except Exception as exc:
        LOG.warning("Direct provider request failed (%s), using browser",
                    type(exc).__name__)
    with provider_timing("browser"):
        cookies, csrf = await browser_cookies(config["site_url"])
    try:
        result = await _request(config, form, cookies=cookies, csrf=csrf)
    except ProviderRateLimit:
        raise
    except Exception as exc:
        # Only log known transport categories. Request text can contain private data.
        category = str(exc)
        safe = {"Provider API returned invalid JSON", "Provider API returned invalid data",
                "Official source did not provide a CSRF token", "Official source rejected CSRF refresh"}
        if category not in safe and not re.fullmatch(r"Provider API HTTP [1-5][0-9]{2}", category):
            category = type(exc).__name__
        LOG.warning("Provider response unavailable: %s", category)
        raise RuntimeError("Official provider request unavailable") from None
    _BROWSER_SESSION_CACHE[key] = (
        time.monotonic() + _BROWSER_SESSION_TTL, dict(cookies), csrf,
    )
    return result


async def _read_json(url):
    try:
        async with AsyncSession(timeout=22) as session:
            with provider_timing("json"):
                response = await session.get(url, allow_redirects=False)
            if response.status_code == 429:
                raise ProviderRateLimit("Official source rate limited requests")
            if response.status_code != 200:
                raise RuntimeError("Official JSON endpoint unavailable")
            return response.json()
    except ProviderRateLimit:
        raise
    except Exception:
        raise RuntimeError("Official JSON request failed") from None


async def get_homes(config, city, street):
    if config.get("adapter") == "mapped_json_v1":
        from mapped_provider import homes
        document = await _read_json(config["site_url"])
        return parse_homes(homes(document, config["mapping"], city, street))
    raw = await ajax_post(config, form_data(
        "getHomeNum",
        [("city", city), ("street", street)],
        update_fact="01.01.2000 00:00",
    ))
    return parse_homes(raw, config)


async def get_schedule(config):
    if config.get("adapter") == "mapped_json_v1":
        from mapped_provider import schedule
        document = await _read_json(config["ajax_url"])
        preset, fact = schedule(document, config["mapping"])
        return SimpleNamespace(preset=parse_preset(preset), fact=parse_fact(fact))
    raw = await ajax_post(config, {
        "method": "checkDisconUpdate",
        "update": "01.01.2000 00:00",
    })
    return SimpleNamespace(
        preset=parse_preset(raw.get("preset")),
        fact=parse_fact(raw.get("fact")),
    )
