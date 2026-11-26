"""Optional read-only schedule views. Official delivery state stays authoritative."""
import hashlib
import html
import json
from datetime import datetime, timedelta

from bot import KYIV, compact_daily_message, parse_verified, time_range
from house_reliability import verified_house_queues
from provider_transport import parse_fact, parse_preset


def mirror_section(document, region, groups, now):
    if document.get("regionId") != region:
        return ""
    status = document.get("lastUpdateStatus") or {}
    if status.get("ok") is not True:
        return ""
    checked = datetime.fromisoformat(status["at"])
    updated = datetime.fromisoformat(document["lastUpdated"])
    if any(not timestamp.tzinfo or not timedelta(0) <= now - timestamp <= timedelta(minutes=30)
           for timestamp in (checked, updated)):
        return ""
    labels = parse_preset(document["preset"]).time_zone
    ranges = sorted(time_range(value) for value in labels.values())
    if not ranges or ranges[0][0] != 0 or ranges[-1][1] != 1440:
        return ""
    if any(a[1] != b[0] for a, b in zip(ranges, ranges[1:])):
        return ""
    fact = parse_fact(document["fact"])
    sections = []
    for stamp, rows in sorted(fact.days.items()):
        day = datetime.fromtimestamp(int(stamp), KYIV).date()
        if day not in (now.date(), now.date() + timedelta(days=1)):
            continue
        for group in groups:
            spans = parse_verified(rows.get(group), labels)
            if spans:
                sections.append(compact_daily_message(day, spans, group=group if len(groups) > 1 else None,
                                                      conditional=True))
    if not sections:
        return ""
    return ("Неофіційне джерело: Baskerville42/outage-data-ua.\n"
            + "Час копії: " + updated.astimezone(KYIV).strftime("%d.%m.%Y %H:%M") + "\n"
            + "Офіційний графік не підтверджено.\n\n" + "\n\n".join(sections))


async def mirror_view(region, groups, now):
    import re
    from provider_transport import _read_json
    if not re.fullmatch(r"[a-z0-9-]{1,40}", region):
        raise ValueError("Invalid mirror region")
    document = await _read_json("https://raw.githubusercontent.com/Baskerville42/outage-data-ua/main/data/"
                                + region + ".json")
    return mirror_section(document, region, groups, now)


def subscription_homes(config, chat):
    from power_config import checkpoint_namespace
    result = {}
    for item in [dict(address=config["address"], label="Основний будинок", chat=chat),
                 *config.get("subscription_homes", [])]:
        group_chat = str(item["chat"])
        if not group_chat.startswith("-") or not group_chat[1:].isdigit():
            continue
        key = checkpoint_namespace(dict(config, address=item["address"]), chat_id=group_chat)[:16]
        result[key] = (str(item["label"]), group_chat, item["address"])
    return result


def is_member(call, chat, user):
    from schedule_board import APIError
    try:
        result = call("getChatMember", chat_id=chat, user_id=int(user))
    except APIError as exc:
        if exc.code in (400, 403):
            return False
        raise
    return result["status"] in ("creator", "administrator", "member") or (
        result["status"] == "restricted" and result.get("is_member") is True)


def subscription_command(msg, homes, state, call, send, persist, callback=None):
    dest = msg.get("chat", {})
    if dest.get("type") != "private":
        return
    user = str(dest.get("id"))
    if callback and str(callback.get("from", {}).get("id")) != user:
        return
    command = (callback.get("data", "") if callback else msg.get("text") or "").strip()
    if command != "/subscriptions" and not command.startswith(("/sub_", "/unsub_")):
        return
    records = state.setdefault("subscriptions", {})
    choices = records.get(user, {})
    buttons = []
    for key, (label, chat, _) in homes.items():
        if not is_member(call, chat, user):
            continue
        if command == "/sub_" + key:
            choices.setdefault(key, "")
        if command == "/unsub_" + key:
            choices.pop(key, None)
        enabled = key in choices
        buttons.append([{"text": ("☑ " if enabled else "☐ ") + label,
                         "callback_data": ("/unsub_" if enabled else "/sub_") + key}])
    if choices:
        records[user] = choices
    else:
        records.pop(user, None)
    persist()
    from schedule_board import APIError, quiet
    text = "Особисті підписки. Натисніть на будинок." if buttons else "Доступних будинків немає."
    params = dict(chat_id=dest["id"], text=text, reply_markup={"inline_keyboard": buttons})
    try:
        if callback:
            call("editMessageText", **params, message_id=msg["message_id"])
        else:
            call("sendMessage", **params, disable_notification=quiet(datetime.now(KYIV)))
    except APIError as exc:
        if exc.code != 403 and not (callback and exc.code == 400 and
                any(reason in exc.description for reason in ("not modified", "not found", "can't be edited"))):
            raise
    if callback:
        try:
            call("answerCallbackQuery", callback_query_id=callback["id"])
        except APIError as exc:
            if exc.code != 400:
                raise


async def notify_subscriptions(homes, state, call, send, persist, primary, primary_groups,
                               source, now, conditional, week="off"):
    from alerts import fetch_address_status
    from schedule_board import APIError, build_board
    records = state.get("subscriptions", {})
    views = {}
    for user, choices in list(records.items()):
        for key in list(choices):
            home = homes.get(key)
            if not home or not is_member(call, home[1], user):
                del choices[key]
                persist()
                continue
            if key not in views:
                if home[2] == primary:
                    groups = primary_groups
                else:
                    try:
                        entry = await fetch_address_status(json.dumps(home[2], ensure_ascii=False))
                        groups = verified_house_queues(entry.group_ids)
                    except Exception:
                        # One unavailable house must not block delivery for other homes.
                        views[key] = None
                        continue
                if not groups:
                    continue
                if source is None:
                    from bot import get_schedule
                    source, _ = await get_schedule(groups)
                # A house-specific emergency at the primary address cannot establish
                # conditions at another address. Mark those hours as conditional.
                tentative = conditional or home[2] != primary
                views[key] = {"daily": build_board({}, source, groups, now, tentative, None, True,
                              parse_verified, compact_daily_message, include_week=week == "combined")}
                if week == "separate":
                    from bot import verified_week, compact_forecast_day
                    monday = now.date() - timedelta(days=now.weekday())
                    for group in groups:
                        forecast = verified_week(source, group)
                        if forecast is not None:
                            label = group if len(groups) > 1 else None
                            views[key]["week:" + group] = "\n\n".join(
                                compact_forecast_day(monday + timedelta(days=n), forecast[n], label, now)
                                for n in range(now.weekday(), 7))
            if views[key] is None:
                continue
            history = choices[key]
            if not isinstance(history, dict):
                history = choices[key] = {"daily": history}
            try:
                for kind, view in views[key].items():
                    digest = hashlib.sha256(view.encode()).hexdigest()
                    if history.get(kind) != digest:
                        send(int(user), html.escape(home[0]) + "\n" + view)
                        history[kind] = digest
                        persist()
            except APIError as exc:
                if exc.code != 403:
                    raise
                records.pop(user, None)
                persist()
                break
        if not choices:
            records.pop(user, None)
            persist()


async def reserve_view(config, groups, now, mirror_enabled):
    from provider_transport import _read_json
    from house_reliability import queue_label
    from urllib.parse import urlsplit
    import logging
    import re
    url = config.get("official_reserve_url")
    if url:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "app.yasno.ua" or parsed.query or parsed.fragment
                or not re.fullmatch(r"/api/blackout-service/public/shutdowns/regions/\d+/dsos/\d+/planned-outages", parsed.path)):
            raise ValueError("Invalid official reserve URL")
        try:
            data = await _read_json(url)
            blocks = []
            for group in groups:
                row = data[queue_label(group)]["today"]
                day = datetime.fromisoformat(row["date"]).astimezone(KYIV).date()
                if day != now.date():
                    break
                if row["status"] != "ScheduleApplies":
                    # An explicitly unpublished or suspended schedule is an official answer.
                    return ""
                boundary = 0
                spans = []
                for slot in row["slots"]:
                    start, end = slot["start"], slot["end"]
                    if (type(start) is not int or type(end) is not int or start != boundary
                            or not start < end <= 1440 or slot["type"] not in ("Definite", "NotPlanned", "Possible")):
                        raise ValueError("Invalid official reserve slots")
                    boundary = end
                    if slot["type"] != "NotPlanned":
                        spans.append((start, end, "off" if slot["type"] == "Definite" else "maybe"))
                if boundary != 1440:
                    break
                label = group if len(groups) > 1 else None
                if not spans:
                    header = compact_daily_message(day, [], group=label).splitlines()[0]
                    blocks.append(header + "\nВідключень за графіком немає")
                else:
                    blocks.append(compact_daily_message(day, spans, group=label, conditional=True))
            if blocks and len(blocks) == len(groups):
                return "Резервне джерело: YASNO.\n" + "\n\n".join(blocks)
        except Exception as exc:
            logging.getLogger(__name__).warning("Official reserve unavailable: %s", type(exc).__name__)
    if mirror_enabled and config.get("mirror_region"):
        return await mirror_view(config["mirror_region"], groups, now)
    return ""
