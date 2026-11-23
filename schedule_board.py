"""Keep one editable schedule separate from delivered alerts."""
import hashlib
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

KYIV = ZoneInfo("Europe/Kyiv")
LOG = logging.getLogger("schedule-board")


def quiet(now):
    return not 9 <= now.astimezone(KYIV).hour < 21


class APIError(RuntimeError):
    def __init__(self, method, code, description):
        super().__init__(f"{method} HTTP {code}")
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
        raise APIError(method, response.status_code, str(body.get("description", "")) if isinstance(body, dict) else "")
    return body["result"]


def build_board(cache, source, groups, now, conditional, address, reliable, parse, format_day):
    from alerts import exact_provider_time
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
    sections = []
    for day in dates:
        date_key = day.isoformat()
        saved = records.setdefault(date_key, {})
        for group in cache.get("groups", []):
            slots = daily.get(day, {}).get(group)
            spans = parse(slots, source.preset.time_zone) if source and slots is not None else None
            label = group if len(cache["groups"]) > 1 else None
            if spans is not None and reliable:
                if spans:
                    saved[group] = format_day(day, spans, group=label, conditional=conditional)
                else:
                    header = format_day(day, [], group=label).splitlines()[0]
                    saved[group] = header + "\nВідключень за графіком немає"
                sections.append(saved[group])
            elif group in saved:
                sections.append(saved[group] + "\nОстанні збережені дані. Оновлення не підтверджено.")
            elif day == dates[0] or slots is not None:
                header = format_day(day, [], group=label).splitlines()[0]
                sections.append(header + "\nПідтверджений графік недоступний")
    if not sections:
        sections.append(now.strftime("%d.%m.%Y") + "\nПідтверджений графік недоступний")
    if address:
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
            if not equivalent:
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
    if pin.get("id") and not top:
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
        pin.update(id=message_id, digest=digest, pinned=False)
        persist()
    if not pin.get("pinned"):
        try:
            call("pinChatMessage", chat_id=chat, message_id=pin["id"], disable_notification=True)
        except APIError as exc:
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


def answer_commands(state, text, chat, call, send, persist):
    updates = call("getUpdates", offset=state.get("board:offset", 0), timeout=0,
                   limit=100, allowed_updates=["message", "channel_post"])
    for item in updates:
        msg = item.get("message") or item.get("channel_post") or {}
        dest = msg.get("chat", {})
        command = re.fullmatch(r"/now(?:@(\w+))?", (msg.get("text") or "").strip(), re.I)
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
