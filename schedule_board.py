"""Keep one editable schedule separate from delivered alerts."""
import hashlib
import logging
import re
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import requests
from astral import Observer
from astral.sun import sunset
from geonamescache import GeonamesCache

from power_config import provider_config

KYIV = ZoneInfo("Europe/Kyiv")
LOG = logging.getLogger("schedule-board")


@lru_cache(maxsize=1)
def city_observer():
    config = provider_config(required=False)
    names = (config.get("address", {}).get("city", ""), config.get("city_label", ""))
    names = {re.sub(r"^(?:м\.\s*|місто\s+)", "", name.casefold()).strip() for name in names} - {""}
    matches = [city for city in GeonamesCache().get_cities().values()
               if city["countrycode"] == "UA" and any(alias.casefold() in names
               for alias in (city["name"], *city["alternatenames"]))]
    if len(matches) == 1:
        return Observer(matches[0]["latitude"], matches[0]["longitude"])
    LOG.warning("Sunset city unresolved; resident notifications will be silent")


@lru_cache(maxsize=1)
def city_sunset(day):
    observer = city_observer()
    return sunset(observer, day, KYIV) if observer else None


def quiet(now):
    local = now.astimezone(KYIV)
    if not 10 <= local.hour < 20:
        return True
    end = city_sunset(local.date())
    return end is None or local >= end


class APIError(RuntimeError):
    def __init__(self, method, code, description, migrated=None):
        super().__init__(f"{method} HTTP {code}")
        self.migrated = migrated
        self.code = code
        self.description = description.lower()


def api(token, method, **params):
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                                 json=params, timeout=12)
        body = response.json()
    except (requests.RequestException, ValueError):
        raise RuntimeError(f"{method} unavailable") from None
    if not isinstance(body, dict) or not body.get("ok"):
        error = body if isinstance(body, dict) else {}
        description = str(error.get("description", ""))
        for reason in ("chat not found", "not enough rights", "upgraded to a supergroup", "can't parse entities", "message thread not found", "group chat was deactivated", "message is too long"):
            if reason in description.lower():
                LOG.warning("Telegram %s: %s", method, reason)
                break
        raise APIError(method, response.status_code, description, (error.get("parameters") or {}).get("migrate_to_chat_id"))
    return body["result"]


def build_board(cache, source, groups, now, conditional, address, reliable, parse, format_day,
                include_week=False, corrected_address=False):
    groups = sorted(groups or [])
    if groups and groups != cache.get("groups"):
        cache.clear()
        cache["groups"] = groups
    dates = [now.date(), now.date() + timedelta(days=1)]
    records = cache.setdefault("records", {})
    for key in list(records):
        if key not in {d.isoformat() for d in dates}:
            del records[key]
    daily = {}
    if source and source.fact:
        for stamp, rows in source.fact.days.items():
            daily[datetime.fromtimestamp(int(stamp), KYIV).date()] = rows
    corrected_window = None
    if corrected_address and address:
        from alerts import exact_provider_time
        start, end = exact_provider_time(address.start_date), exact_provider_time(address.end_date)
        if start and end and start <= now < end:
            corrected_window = start, end
    sections = []
    for day in dates:
        date_key = day.isoformat()
        saved = records.setdefault(date_key, {})
        for group in cache.get("groups", []):
            slots = daily.get(day, {}).get(group)
            spans = parse(slots, source.preset.time_zone) if source and slots is not None else None
            label = group if len(cache["groups"]) > 1 else None
            if spans is not None and reliable:
                if corrected_window and day == now.date() and not conditional:
                    start, end = corrected_window
                    midnight = datetime.combine(day, datetime.min.time(), KYIV)
                    if start.date() == day:
                        spans = [(start.hour * 60 + start.minute, b, mode)
                                 if mode == "off" and midnight + timedelta(minutes=a) <= now
                                 and midnight + timedelta(minutes=b) == end else (a, b, mode)
                                 for a, b, mode in spans]
                if spans:
                    saved[group] = format_day(day, spans, group=label, conditional=conditional)
                else:
                    header = format_day(day, [], group=label).splitlines()[0]
                    saved[group] = header + "\nВідключень за графіком немає"
                sections.append(saved[group])
            elif group in saved:
                sections.append(saved[group] + "\nОстанні збережені дані. Оновлення не підтверджено.")
            elif not include_week and (day == dates[0] or slots is not None):
                header = format_day(day, [], group=label).splitlines()[0]
                sections.append(header + "\nПідтверджений графік недоступний")
    if include_week:
        from bot import compact_forecast_day, upcoming_spans, verified_week

        monday = dates[0] - timedelta(days=dates[0].weekday())
        forecast = cache.setdefault("forecast", {})
        if forecast.get("week") != monday.isoformat():
            forecast.clear()
            forecast["week"] = monday.isoformat()
        for group in cache.get("groups", []):
            week = verified_week(source, group) if source and reliable and group in groups else None
            if week is not None:
                forecast.setdefault("groups", {})[group] = week
            saved_week = forecast.get("groups", {}).get(group)
            label = group if len(cache["groups"]) > 1 else None
            for n in range(7):
                day = dates[0] + timedelta(days=n)
                if group in records.get(day.isoformat(), {}):
                    continue
                if not saved_week or day >= monday + timedelta(days=7):
                    continue
                spans = upcoming_spans(day, saved_week[day.weekday()], now)
                if not spans:
                    continue
                block = compact_forecast_day(day, spans, label)
                if week is None:
                    block += "\n(останні збережені дані)"
                sections.append(block)
    if not sections:
        sections.append(now.strftime("%d.%m.%Y") + "\nПідтверджений графік недоступний")
    if address and not include_week:
        from alerts import exact_provider_time
        start = exact_provider_time(address.start_date)
        end = exact_provider_time(address.end_date)
        if start and end and start <= now < end:
            midnight = datetime.combine(now.date(), datetime.min.time(), KYIV)
            equivalent = bool(groups and reliable and not conditional)
            for group in groups:
                rows = daily.get(now.date(), {}).get(group)
                spans = parse(rows, source.preset.time_zone) if source and rows is not None else []
                equivalent &= any(midnight + timedelta(minutes=a) <= now
                                  and midnight + timedelta(minutes=b) == end and kind == "off"
                                  for a, b, kind in spans or [])
            if corrected_window and (not equivalent or start.date() != now.date()):
                sections.append("За адресою вказано відключення: " + start.strftime("%d.%m.%Y %H:%M")
                                + "–" + end.strftime("%d.%m.%Y %H:%M"))
            elif not equivalent:
                sections.append("За адресою вказано відключення до " + end.strftime("%d.%m.%Y %H:%M"))
    return "\n\n".join(sections)


def update_pin(state, text, chat, call, send, persist):
    pin = state.setdefault("board:pin", {})
    digest = hashlib.sha256(text.encode()).hexdigest()
    top = call("getChat", chat_id=chat).get("pinned_message") or {}
    if not pin.get("id") and not pin.get("previous") and top:
        previous = top.get("text") or ""
        owner = str(top.get("from", {}).get("id"))
        if owner == state.get("board:owner") and re.match(r"^\d{2}\.\d{2}\.\d{4}[^\n]*\n(?:Відключення|Можливі відключення)", previous):
            pin["previous"] = top["message_id"]
    if pin.get("id") and top.get("message_id") != pin["id"]:
        pin["pinned"] = False
    if pin.get("id") and pin.get("digest") != digest:
        try:
            call("editMessageText", chat_id=chat, message_id=pin["id"], text=text, parse_mode="HTML")
        except APIError as exc:
            if exc.code == 400 and "message to edit not found" in exc.description:
                pin.clear()
            elif exc.code != 400 or "message is not modified" not in exc.description:
                raise
        if pin.get("id"):
            pin["digest"] = digest
            persist()
    if not pin.get("id"):
        message_id = send(chat, text)
        if not isinstance(message_id, int) or message_id <= 0:
            raise RuntimeError("Missing schedule message ID")
        LOG.info("Schedule board sent")
        pin.update(id=message_id, digest=digest, pinned=False)
        persist()
    if not pin.get("pinned"):
        try:
            call("pinChatMessage", chat_id=chat, message_id=pin["id"], disable_notification=quiet(datetime.now(KYIV)))
        except APIError as exc:
            for detail in ("not enough rights", "chat_admin_required", "not an administrator", "upgraded to a supergroup", "message can't be pinned", "chat not found", "message to pin not found"):
                if detail in exc.description:
                    LOG.warning("Telegram pin response: %s", detail)
                    break
            if exc.code == 400 and "not found" in exc.description:
                pin.clear()
                persist()
            raise
        pin["pinned"] = True
        persist()
        LOG.info("Schedule board pinned")
    if pin.get("previous") and pin["previous"] != pin["id"]:
        try:
            call("unpinChatMessage", chat_id=chat, message_id=pin["previous"])
        except APIError as exc:
            if exc.code != 400 or not any(t in exc.description for t in ("not found", "not pinned")):
                raise
        pin.pop("previous")
        persist()


def answer_commands(state, text, chat, call, send, persist, menu=None, now_enabled=True):
    updates = call("getUpdates", offset=state.get("board:offset", 0), timeout=0,
                   limit=100, allowed_updates=["message", "channel_post"] + (["callback_query"] if menu else []))
    for item in updates:
        msg = item.get("message") or item.get("channel_post") or {}
        if menu is not None:
            callback = item.get("callback_query")
            menu(callback.get("message", {}) if callback else msg, callback)
        dest = msg.get("chat", {})
        command = re.fullmatch(r"/now(?:@(\w+))?", (msg.get("text") or "").strip(), re.I)
        if not now_enabled:
            command = None
        if command and command[1]:
            username = state.get("board:username")
            if not username or username.casefold() != command[1].casefold():
                username = call("getMe")["username"]
                state["board:username"] = username
            if username.casefold() != command[1].casefold():
                command = None
        if command:
            allowed = str(dest.get("id")) == str(chat)
            if not allowed and dest.get("type") == "private" and str(chat).startswith("-"):
                try:
                    member = call("getChatMember", chat_id=chat, user_id=dest["id"])
                    allowed = member["status"] in {"creator", "administrator", "member"} or (
                        member["status"] == "restricted" and member.get("is_member"))
                except APIError as exc:
                    if exc.code not in (400, 403):
                        raise
            if allowed:
                try:
                    send(dest["id"], text)
                except APIError as exc:
                    if exc.code != 403:
                        raise
        state["board:offset"] = item["update_id"] + 1
        persist()
