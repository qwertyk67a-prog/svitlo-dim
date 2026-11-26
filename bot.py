"""Automatic outage schedule notifications for one configured home."""
import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

KYIV = ZoneInfo("Europe/Kyiv")
from power_config import provider_config, operator_label, official_site, address_json, checkpoint_namespace
from house_reliability import queue_label
LOG = logging.getLogger("outage-bot")


COMPACT_DAILY_SCHEDULE = True


def compact_forecast_day(day, spans, group=None, observed_at=None):
    header = day.strftime("%d.%m.%Y")
    if group is not None:
        header += " · Черга " + html.escape(queue_label(group))
    times = [f"{clock(a)}–{clock(b)}" for a, b in upcoming_spans(day, spans, observed_at)]
    empty = "До кінця доби відключень не передбачено" if spans and not times else "Відключень не передбачено"
    return "\n".join([header, *(times or [empty])])


def compact_daily_message(day, spans, *, group=None, conditional=False):
    date_line = day.strftime("%d.%m.%Y")
    if group is not None:
        date_line += " · Черга " + html.escape(queue_label(group))
    lines = [date_line]
    if not spans:
        lines.append("Відключення за графіком скасовано")
        return "\n".join(lines)
    for mode, label in (("off", "Відключення"), ("maybe", "Можливі відключення")):
        if conditional and mode == "off":
            continue
        times = [f"{clock(a)}–{clock(b)}" for a, b, status in spans
                 if conditional or status == mode]
        if times:
            lines.extend([label + ":", *times])
    return "\n".join(lines)


def address_window(record):
    from alerts import exact_provider_time
    if not isinstance(record, dict) or record.get("status") != "active":
        return None, None
    return exact_provider_time(record.get("start")), exact_provider_time(record.get("end"))


def same_remaining_window(first, second, now):
    start, end = first
    other_start, other_end = second
    return bool(start and other_start and end and end == other_end and end > now
                and max(start, now) == max(other_start, now))


def address_already_announced(value, old, state, groups, now, conditional):
    window = address_window(value)
    start, end = window
    old_start, old_end = address_window(old)
    if same_remaining_window(window, (old_start, old_end), now):
        return True
    if (start and old_start and not end and not old_end
            and value.get("end") == old.get("end") and max(start, now) == max(old_start, now)):
        return True
    if not start or not end or end <= now or not groups or conditional:
        return False
    if old_end and old_end > now and (old_start or old_end != end):
        return False
    day = max(start, now).date()
    midnight = datetime.combine(day, datetime.min.time(), KYIV)
    for group in groups:
        published = state.get(f"{day.isoformat()}:{group}") or {}
        if not isinstance(published, dict) or published.get("status") != "outages" or published.get("conditional"):
            return False
        if not any(mode == "off" and same_remaining_window(window, (
            midnight + timedelta(minutes=a), midnight + timedelta(minutes=b)), now)
                   for a, b, mode in published.get("spans", ())):
            return False
    return True


def compact_address_message(value, now):
    from alerts import parse_provider_date
    if value["status"] == "expired":
        return (now.strftime("%d.%m.%Y") + "\nОновлено час завершення:\n"
                + html.escape(value["end"]))
    start, end = address_window(value)
    day = start or parse_provider_date(value.get("start")) or end or now

    def display(raw, instant):
        if instant:
            return instant.strftime("%H:%M" if instant.date() == day.date() else "%d.%m.%Y %H:%M")
        return html.escape(str(raw or ""))

    beginning = display(value.get("start"), start)
    finish = display(value.get("end"), end)
    if beginning and finish:
        detail = beginning + "–" + finish
    elif finish:
        detail = "До " + finish
    elif beginning:
        detail = "З " + beginning + ", час завершення невідомий"
    else:
        detail = "Час не вказано"
    return day.strftime("%d.%m.%Y") + "\nЗа повідомленням оператора для будинку:\n" + detail


def address_notice_with_schedule(value, now, source, groups, conditional):
    message = compact_address_message(value, now)
    start, end = address_window(value)
    if not (start and end and start.date() == now.date() and source and source.fact and groups):
        return message
    day = start.date()
    midnight = datetime.combine(day, datetime.min.time(), KYIV)
    for stamp, rows in source.fact.days.items():
        if datetime.fromtimestamp(int(stamp), KYIV).date() != day:
            continue
        for group in groups:
            spans = parse_verified(rows.get(group), source.preset.time_zone)
            later = [(a, b, mode) for a, b, mode in spans or ()
                     if midnight + timedelta(minutes=a) >= end
                     and midnight + timedelta(minutes=b) > now]
            if later:
                section = compact_daily_message(day, later, group=group if len(groups) > 1 else None,
                                                conditional=conditional)
                header, _, body = section.partition("\n")
                body = body.removeprefix("Відключення:\n")
                if len(groups) > 1:
                    body = header.partition(" · ")[2] + "\n" + body
                message += "\n\nДалі за графіком:\n" + body
    return message


def time_range(label):
    """Parse provider hourly '00-01' and minute-precision '00:00–01:00' slots."""
    source = str(label).strip()
    source = re.sub(r"\s+", "", source).replace("–", "-").replace("—", "-")
    hourly = re.fullmatch(r"(\d{1,2})-(\d{1,2})", source)
    if hourly:
        start_hour, end_hour = map(int, hourly.groups())
        if 0 <= start_hour < 24 and start_hour < end_hour <= 24:
            return start_hour * 60, end_hour * 60
        raise ValueError(f"Неверный часовой интервал постачальник: {label}")
    minute = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", source)
    if not minute:
        raise ValueError(f"Неверная подпись времени: {label}")
    sh, sm, eh, em = map(int, minute.groups())
    if sh > 23 or eh > 24 or sm > 59 or em > 59 or (eh == 24 and em != 0):
        raise ValueError(f"Неверный интервал времени: {label}")
    a, b = sh * 60 + sm, eh * 60 + em
    if a >= b:
        raise ValueError(f"Неверный интервал времени: {label}")
    return a, b


def intervals(slots, labels):
    if not slots or not labels:
        raise RuntimeError("Нет достоверных интервалов постачальник")
    spans = []
    unknown = 0
    for key, status in slots.items():
        if str(key) not in labels:
            raise RuntimeError(f"Нет подписи времени для слота {key}")
        a, b = time_range(labels[str(key)])
        mid = (a + b) // 2
        val = str(getattr(status, "value", status)).lower()
        chunks = {
            "no": [(a, b, "off")],
            "first": [(a, mid, "off")],
            "second": [(mid, b, "off")],
            "maybe": [(a, b, "maybe")],
            "mfirst": [(a, mid, "maybe")],
            "msecond": [(mid, b, "maybe")],
            "yes": [],
        }.get(val)
        if chunks is None:
            unknown += 1
        else:
            spans.extend(chunks)
    spans.sort()
    joined = []
    for a, b, mode in spans:
        if joined and joined[-1][2] == mode and joined[-1][1] == a:
            joined[-1] = (joined[-1][0], b, mode)
        else:
            joined.append((a, b, mode))
    return joined, unknown


def clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def minutes_text(minutes):
    """Ukrainian duration; fractional hours stay precise to the minute."""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} год {mins} хв"
    if hours:
        return f"{hours} год"
    return f"{mins} хв"


def duration(spans, kind):
    """Sum lengths without counting potential outages as confirmed."""
    parts = sorted((a, b) for a, b, status in spans if status == kind)
    total = 0
    if not parts:
        return 0
    start, end = parts[0]
    for a, b in parts[1:]:
        if a <= end:
            end = max(end, b)
        else:
            total += end - start
            start, end = a, b
    return total + end - start


def subtract_spans(before, after):
    """Return merged portions of intervals in before but not in after."""
    result = []
    for a, b in sorted(before):
        remaining = [(a, b)]
        for c, d in sorted(after):
            next_parts = []
            for start, end in remaining:
                if d <= start or c >= end:
                    next_parts.append((start, end))
                else:
                    if start < c:
                        next_parts.append((start, c))
                    if d < end:
                        next_parts.append((d, end))
            remaining = next_parts
        result.extend(remaining)
    merged = []
    for a, b in sorted(result):
        if merged and merged[-1][1] == a:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def describe_changes(previous, current):
    """Explain removed and added time ranges when prior intervals are available."""
    if previous is None:
        return []
    lines = []
    names = (
        ("off", "✅ Прибрано із запланованих:", "❗ Додано заплановані:"),
        ("maybe", "🟢 Прибрано із можливих:", "🟠 Додано можливі:"),
    )
    for kind, removed_title, added_title in names:
        old = [(a, b) for a, b, status in previous if status == kind]
        new = [(a, b) for a, b, status in current if status == kind]
        removed = subtract_spans(old, new)
        added = subtract_spans(new, old)
        if removed:
            lines.append(removed_title)
            lines.extend(f"• {clock(a)}–{clock(b)}" for a, b in removed)
        if added:
            lines.append(added_title)
            lines.extend(f"• {clock(a)}–{clock(b)}" for a, b in added)
    return lines


def upcoming_spans(day, spans, observed_at):
    """Keep current/future GPV intervals; ignore those fully over today.

    This does not prove electricity has returned. It only avoids disturbing
    residents with late official information about a past interval.
    """
    if observed_at is None or day != observed_at.astimezone(KYIV).date():
        return list(spans or ())
    t = observed_at.astimezone(KYIV)
    minute = t.hour * 60 + t.minute
    return [tuple(s) for s in (spans or ()) if s[1] > minute]


def upcoming_signature(day, spans, observed_at):
    """Compare only remaining plan, clipped at the moment of observation."""
    remaining = upcoming_spans(day, spans, observed_at)
    if observed_at is None or day != observed_at.astimezone(KYIV).date():
        return remaining
    t = observed_at.astimezone(KYIV)
    minute = t.hour * 60 + t.minute
    return [(max(start, minute), end, status)
            for start, end, status in remaining]


def newly_current_schedule_outage(day, spans, previous, observed_at):
    """A provider-confirmed slot covers NOW but prior published plan did not.

    This is a statement about the SCHEDULE, never voltage at the house.
    """
    if observed_at is None:
        return False
    instant = observed_at.astimezone(KYIV)
    if instant.date() != day:
        return False
    minute = instant.hour * 60 + instant.minute
    now_off = any(a <= minute < b and status == "off"
                  for a, b, status in spans)
    before_off = any(a <= minute < b and status == "off"
                     for a, b, status in (previous or ()))
    return now_off and not before_off


def day_message(day, group, spans, updated=None, event="new", previous=None,
                observed_at=None, total_spans=None):
    """Ukrainian daily message, only when there are scheduled/possible outages."""
    if not spans:
        return None
    titles = {
        "new": "⚡️ <b>Графік відключень</b>",
        "changed": "🔄 <b>Графік відключень змінено</b>",
        "resumed": "⚠️ <b>Відключення знову заплановано</b>",
    }
    lines = [
        titles[event],
        f"📅 {day:%d.%m.%Y} · Черга {html.escape(queue_label(group))}",
        f"✅ <b>Добовий графік {html.escape(operator_label())} має пріоритет</b> над попереднім "
        "тижневим прогнозом на цю дату.",
    ]
    if newly_current_schedule_outage(day, spans, previous, observed_at):
        lines.extend([
            "",
            "⚠️ <b>За оновленим графіком час відключення вже настав.</b>",
            "оператор міг повідомити про зміну пізніше фактичного початку.",
            "Графік не підтверджує стан електропостачання конкретної квартири.",
        ])
    if event == "changed":
        details = describe_changes(previous, spans)
        if details:
            lines.extend(["", "<b>Що змінилося:</b>", *details])
    for kind, title in (("off", "🔴 Заплановані відключення:"),
                        ("maybe", "🟠 Можливі відключення:")):
        selected = [(a, b) for a, b, status in spans if status == kind]
        if selected:
            lines.extend(["", f"<b>{title}</b>"])
            lines.extend(f"• {clock(a)}–{clock(b)}" for a, b in selected)
    # Current/future intervals are displayed; totals cover the whole day.
    totals = spans if total_spans is None else total_spans
    confirmed_minutes = duration(totals, "off")
    possible_minutes = duration(totals, "maybe")
    if confirmed_minutes:
        lines.append(f"\n⏱ Загалом за добу за графіком: <b>{minutes_text(confirmed_minutes)}</b>")
    if possible_minutes:
        lines.append(f"⏱ Можливі відключення за добу: <b>{minutes_text(possible_minutes)}</b>")
    if updated:
        lines.append("\nОновлено: " + html.escape(str(updated)))
    lines.append(f'<a href="{html.escape(official_site(), quote=True)}">Джерело: {html.escape(operator_label())}</a>')
    return "\n".join(lines)


def cancellation_message(day, group, updated=None, remaining_today=False):
    """Cancellation is a meaningful change only after a prior outage notice."""
    lines = [
        "✅ <b>Планові відключення скасовано</b>",
        f"📅 {day:%d.%m.%Y} · Черга {html.escape(queue_label(group))}",
        "",
        ("На решту дня відключення за оновленим графіком не заплановані."
         if remaining_today else "За оновленим графіком планових відключень немає."),
    ]
    if updated:
        lines.append("\nОновлено: " + html.escape(str(updated)))
    lines.append(f'<a href="{html.escape(official_site(), quote=True)}">Джерело: {html.escape(operator_label())}</a>')
    return "\n".join(lines)


def weekly_message(monday, group, entries, changed=False,
                   observed_at=None, confirmed_days=()):
    """Show only relevant weekly preview days not covered by provider daily facts.

    This is PRESENTATION ONLY: weekly candidate fingerprints remain based on
    the original seven-day forecast, so the passage of midnight never causes
    a duplicate "forecast changed" notification.
    """
    confirmed = set(confirmed_days)
    current_day = (observed_at.astimezone(KYIV).date()
                   if observed_at is not None else monday)
    visible = [
        (day, spans) for day, spans in entries
        if day >= current_day and day not in confirmed
    ]
    if not visible:
        return None
    sunday = monday + timedelta(days=6)
    lines = [
        "🔄 <b>Тижневий прогноз змінено</b>" if changed else
        "📆 <b>Тижневий прогноз можливих відключень</b>",
        f"Черга {html.escape(queue_label(group))} · {monday:%d.%m}–{sunday:%d.%m.%Y}",
    ]
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
    for day, spans in visible:
        lines.append(f"\n<b>{weekdays[day.weekday()]} {day:%d.%m}</b>")
        lines.extend(f"• {clock(a)}–{clock(b)}" for a, b in spans)
    lines.append(
        "\n⚠️ Попередній прогноз можливих відключень. "
        "Минулі дати та дні з підтвердженим добовим графіком не показані. "
        f"Для таких днів використовуйте окремий добовий графік {html.escape(operator_label())}."
    )
    lines.append(f'<a href="{html.escape(official_site(), quote=True)}">Джерело: {html.escape(operator_label())}</a>')
    return "\n".join(lines)


def weekly_cancellation_message(monday, group):
    """Only for complete, explicit all-white weekly forecast after a prior notice."""
    sunday = monday + timedelta(days=6)
    return "\n".join([
        "✅ <b>Тижневий прогноз відключень скасовано</b>",
        f"Черга {html.escape(queue_label(group))} · {monday:%d.%m}–{sunday:%d.%m.%Y}",
        "",
        "У оновленій тижневій таблиці планових відключень немає.",
        f'<a href="{html.escape(official_site(), quote=True)}">Джерело: {html.escape(operator_label())}</a>',
    ])


def state_digest(record):
    """Read legacy SHA strings and the new structured state records."""
    if isinstance(record, str):
        return record
    if isinstance(record, dict):
        return record.get("digest")
    return None


def state_status(record):
    """Historical SHA entries were only created for positive outage notices."""
    if isinstance(record, str):
        return "outages"
    if isinstance(record, dict):
        return record.get("status")
    return None


def fingerprint(payload):
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def parse_verified(slots, labels):
    """Refuse incomplete/unknown tables, especially for all-clear decisions."""
    if not isinstance(slots, dict) or not slots:
        return None
    if set(slots) != set(labels):
        return None
    try:
        spans, unknown = intervals(slots, labels)
    except (ValueError, RuntimeError):
        return None
    if unknown:
        return None
    return spans


def verified_week(source, group):
    """Return all seven days, including verified empty days, or no forecast."""
    preset = source.preset
    plan = preset.groups.get(group) if preset and preset.is_active else None
    if not plan:
        return None
    week = []
    for weekday in range(1, 8):
        item = plan.get_day(weekday)
        spans = parse_verified(item.slots, preset.time_zone) if item else None
        if spans is None:
            return None
        week.append(tuple((a, b) for a, b, _ in spans))
    return week


def weekly_notice(entries, activation, digest, previous, now):
    """Compare both verified schedules at one Kyiv time, including the active slot."""
    days = {day.isoformat(): [list(span) for span in spans] for day, spans in entries}
    record = {"status": "outages", "digest": digest, "days": days, "activation": activation}
    if state_digest(previous) == digest:
        return record, False
    if not isinstance(previous, dict) or previous.get("activation") != activation:
        return record, True
    old_days = previous.get("days")
    if not isinstance(old_days, dict):
        # An older checkpoint has only a hash. A changed plan may affect the future.
        return record, True

    local = now.astimezone(KYIV)
    today = local.date().isoformat()
    minute = local.hour * 60 + local.minute

    def remaining(schedule):
        return [(day, max(start, minute) if day == today else start, end)
                for day, spans in sorted(schedule.items()) if day >= today
                for start, end in spans if day > today or end > minute]

    return record, remaining(old_days) != remaining(days)


def collect_notifications(source, group, today, state=None, report_empty_week=False):
    """
    Return notification candidates only for actual changes.

    Missing/unreadable data are not equivalent to cancellation.
    Complete all-white tables are cancellation evidence only if state
    proves that a positive notice was sent previously.
    """
    state = state or {}
    labels = source.preset.time_zone if source.preset else None
    if not labels:
        LOG.info("Немає часових інтервалів оператор; публікацій немає")
        return []

    actual = {}
    if source.fact:
        for stamp, group_map in source.fact.days.items():
            day = datetime.fromtimestamp(int(stamp), KYIV).date()
            if day in (today, today + timedelta(days=1)):
                actual[day] = group_map

    result = []
    for day in (today, today + timedelta(days=1)):
        key = f"{day.isoformat()}:{group}"
        slots = actual.get(day, {}).get(group)
        spans = parse_verified(slots, labels)
        if spans is None:
            continue
        if spans:
            # Maintain the legacy hash to avoid duplicate announcements
            # when deploying the new state-aware bot.
            payload = (day.isoformat(), group, True, spans, 0)
            result.append((key, payload, "day", (day, spans, source.fact.update)))
        elif state_status(state.get(key)) == "outages":
            payload = (day.isoformat(), group, "cancelled")
            result.append((key, payload, "day_cancel", (day, source.fact.update)))

    monday = today - timedelta(days=today.weekday())
    week_key = f"week:{monday.isoformat()}:{group}"
    week = verified_week(source, group)
    all_seven_verified = week is not None
    entries = [(monday + timedelta(days=n), spans)
               for n, spans in enumerate(week or []) if spans]

    if all_seven_verified and (entries or report_empty_week):
        # Maintain prior weekly hash for previously sent weekly forecasts.
        payload = (monday.isoformat(), group,
                   tuple((day.isoformat(), spans) for day, spans in entries))
        result.append((week_key, payload, "week", (monday, entries)))
    elif all_seven_verified and not entries and state_status(state.get(week_key)) == "outages":
        payload = (monday.isoformat(), group, "cancelled")
        result.append((week_key, payload, "week_cancel", (monday,)))

    return result


def _queue_tokens():
    """Pseudonym mapping from the private queue catalog, not a code constant."""
    cfg = provider_config()
    return {
        group: "Q" + hmac.new(
            cfg["state_key"].encode("utf-8"),
            group.encode("utf-8"), hashlib.sha256,
        ).hexdigest()[:24]
        for group in cfg["queue_catalog"]
    }


def _map_state_key(key, mapping):
    """Change only queue components of known checkpoint keys."""
    if not isinstance(key, str):
        return key
    match = re.fullmatch(r"((?:week:|reminder:)?\d{4}-\d{2}-\d{2}:)(.+)", key)
    if not match:
        return key
    prefix, queue = match.groups()
    suffix = ""
    if prefix.startswith("reminder:"):
        parts = queue.rsplit(":", 2)
        if len(parts) != 3 or not all(part.isdigit() for part in parts[1:]):
            return key
        queue, start, lead = parts
        suffix = f":{start}:{lead}"
    if queue in mapping:
        return prefix + mapping[queue] + suffix
    # Restore older checkpoints that pseudonymized only a composite ID's prefix.
    head, separator, tail = queue.partition(":")
    if separator and re.fullmatch(r"Q[0-9a-f]{24}", head) and head in mapping:
        return prefix + mapping[head] + ":" + tail + suffix
    return key


def _map_state_values(value, mapping):
    """Never replace short queue names inside dates, hashes or arbitrary text."""
    if isinstance(value, dict):
        return {
            _map_state_key(key, mapping): _map_state_values(item, mapping)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_map_state_values(item, mapping) for item in value]
    if isinstance(value, tuple):
        return [_map_state_values(item, mapping) for item in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def _anonymize_state(key):
    return _map_state_key(key, _queue_tokens())


def _restore_state(serialized):
    """Compatibility helper for tests that supply a serialized checkpoint."""
    reverse = {token: group for group, token in _queue_tokens().items()}
    data = json.loads(serialized)
    return json.dumps(_map_state_values(data, reverse), ensure_ascii=False)


def state_read(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    reverse = {token: group for group, token in _queue_tokens().items()}
    return _map_state_values(data, reverse)


def state_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    public = _map_state_values(data, _queue_tokens())
    temp.write_text(json.dumps(
        public, ensure_ascii=False, sort_keys=True, indent=2,
    ), encoding="utf-8")
    temp.replace(path)


def telegram_send(token, chat, text, deadline=None):
    """Respect Telegram 429 RetryAfter; never send after reminder deadline.

    Telegram sendMessage has no idempotency key. A network timeout after
    delivery can cause a duplicate on retry; we do not claim exactly once.
    """
    from schedule_board import quiet
    for attempt in range(3):
        if deadline is not None and datetime.now(KYIV) >= deadline:
            raise RuntimeError("Термін нагадування минув; застаріле повідомлення не надсилається")
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": "true",
                      "disable_notification": str(quiet(datetime.now(KYIV))).lower()},
                timeout=20,
            )
        except requests.RequestException:
            raise RuntimeError("Немає зв'язку з Telegram; доставка невідома") from None
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError(f"Некоректна відповідь Telegram HTTP {response.status_code}") from None
        if response.status_code == 200 and body.get("ok") is True:
            return body.get("result", {}).get("message_id")
        migrated = (body.get("parameters") or {}).get("migrate_to_chat_id")
        if response.status_code == 400 and isinstance(migrated, int) and migrated < 0 and str(migrated) != str(chat):
            chat = migrated
            continue
        if response.status_code == 429:
            delay = (body.get("parameters") or {}).get("retry_after", 1)
            try:
                delay = max(1, int(delay))
            except (TypeError, ValueError):
                delay = 1
            if attempt < 2 and delay <= 12 and (
                deadline is None or datetime.now(KYIV) + timedelta(seconds=delay + 1) < deadline
            ):
                time.sleep(delay)
                continue
            raise RuntimeError(f"Telegram обмежив швидкість, повтор через {delay} с")
        raise RuntimeError(f"Telegram відхилив повідомлення: HTTP {response.status_code}")
    raise RuntimeError("Telegram: кількість повторів вичерпано")


async def get_schedule(group_ids=None):
    """Read official schedule using the private provider configuration."""
    from house_reliability import verified_house_queues
    from provider_transport import get_schedule as fetch_schedule
    config = provider_config()
    if group_ids is None:
        raise RuntimeError("Потрібно підтвердити черги за адресою")
    group_ids = verified_house_queues(group_ids)
    source = await fetch_schedule(config)
    if not source.preset.time_zone:
        raise RuntimeError("Джерело не повернуло часові інтервали")
    # A partial all-white table cannot establish a cancellation.
    ranges = sorted(time_range(label) for label in source.preset.time_zone.values())
    boundary = 0
    for start, end in ranges:
        if start != boundary:
            raise RuntimeError("Часові інтервали оператора мають пропуск або перетин")
        boundary = end
    if boundary != 1440:
        raise RuntimeError("Часові інтервали оператора не охоплюють повну добу")
    missing = [
        group for group in group_ids
        if group not in source.preset.groups and (
            not source.fact or not any(
                group in day for day in source.fact.days.values()
            )
        )
    ]
    if missing:
        raise RuntimeError("Джерело не містить графіків підтверджених черг")
    LOG.info("Перевірено графіки підтверджених черг: %d", len(group_ids))
    return source, list(group_ids)


async def once():
    """One-house notification run: urgent changes first, reminders only while timely."""
    from house_reliability import (
        due_reminders, house_queue_transition, parse_reminder_minutes,
        trim_state, verified_house_queues, cached_queue_is_fresh,
    )

    weekly_mode = os.getenv("WEEKLY_SCHEDULE_MODE", "off").strip().lower() or "off"
    if weekly_mode not in ("off", "combined", "separate"):
        raise ValueError("WEEKLY_SCHEDULE_MODE must be off, combined or separate")
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not dry_run and (not token or not chat):
        raise RuntimeError("Відсутні TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID")

    # Validate the private configuration before touching any checkpoint.
    config = provider_config()
    path = Path(os.getenv("STATE_PATH", "data/state.json"))
    state = state_read(path)
    # Each cloned repository must ignore another home's notification history.
    # HMAC protects the address identity from straightforward dictionary guesses.
    identity = checkpoint_namespace(config, token, chat)
    if state.get("config:namespace") != identity:
        state = {"config:namespace": identity}

    chat = state.get("board:migrated_chat") or chat

    def persist():
        """Never mutate a real notification checkpoint during a dry run."""
        if not dry_run:
            state_write(path, state)

    now = datetime.now(KYIV)
    today = now.date()
    leads = parse_reminder_minutes(os.getenv("REMINDER_MINUTES", "0"))
    LOG.info("Нагадування, хв: %s", list(leads))
    trim_state(state, today)
    if weekly_mode != state.get("board:weekly_mode", "off"):
        state["board:weekly_mode"] = weekly_mode
        if weekly_mode == "separate":
            state["board:weekly_activation"] = now.isoformat()
    persist()
    problems = []
    source_problems = []
    sent = 0

    # Queue assignments come only from the official address response.
    house = None
    house_groups = None
    queue_change = None
    regional_scope = None
    provider_config()
    private_address = address_json()
    if private_address:
        try:
            from alerts import fetch_address_status
            from event_scope import house_scope_from_secret
            regional_scope = house_scope_from_secret(private_address)
            house = await fetch_address_status(private_address)
            house_groups = verified_house_queues(
                house.group_ids if house is not None else None
            )
            # One checkpoint per hour is sufficient to establish a bounded
            # fallback age without committing new state every five minutes.
            verified_hour = now.replace(minute=0, second=0, microsecond=0).isoformat()
            if (state.get("house:last_verified_at") != verified_hour
                    or state.get("house:verified_groups") != list(house_groups)):
                state["house:verified_groups"] = list(house_groups)
                state["house:last_verified_at"] = verified_hour
                persist()
            house_snapshot, queue_change = house_queue_transition(
                state, house_groups
            )
            # The old approval/hold mechanism is obsolete. The current
            # confirmed queues govern automatic tracking after migration.
            if "house:queue_hold" in state:
                state.pop("house:queue_hold", None)
                persist()
        except Exception as exc:
            LOG.warning("Не вдалося перевірити черги за адресою: %s",
                        type(exc).__name__)
            # Fail the run even if we can process schedules with a recent
            # verified queue. Otherwise an address-source outage looks healthy.
            source_problems.append("address_lookup")
            # An old or un-timestamped assignment is not safe indefinitely.
            cached = state.get("house:verified_groups", state.get("house:groups"))
            if cached and cached_queue_is_fresh(
                    state.get("house:last_verified_at"), now):
                try:
                    house_groups = verified_house_queues(cached)
                    house_snapshot, queue_change = house_queue_transition(state, house_groups)
                    LOG.warning("Тимчасово використовую перевірені черги з кешу")
                except ValueError:
                    house_groups = None
            else:
                LOG.warning("Підтвердження черги застаріле або відсутнє")
    elif state.get("house:queue_hold"):
        LOG.warning("Раніше чергу не підтверджено; потрібен секрет адреси")
        return


    pending = []
    seq = 0

    def enqueue(priority, key, value, message, origin, deadline=None, cursor=None):
        nonlocal seq
        pending.append((priority, seq, key, value, message, origin, deadline, cursor))
        seq += 1

    # Announce queue additions/removals exactly once; never pause the house.
    # A failed announcement leaves the old snapshot for the next check.
    if house_groups is not None:
        if queue_change:
            enqueue(1, "house:groups", house_snapshot, queue_change, "queue_change")
        elif state.get("house:groups") != house_snapshot:
            state["house:groups"] = house_snapshot
            persist()

    # Official emergency and GAV announcements are prioritized.
    if os.getenv("ANNOUNCEMENTS_ENABLED", "true").lower() == "true":
        try:
            from alerts import fetch_official_page, region_candidates
            events = region_candidates(
                fetch_official_page(), state, now, scope=regional_scope
            )
            for post_id, key, value, message in events:
                if key.startswith("official:unresolved:"):
                    source_problems.append("ambiguous_official_incident")
                enqueue(0, key, value, message, "official", cursor=post_id)
            if not events and "official:last_id" in state:
                persist()
        except Exception as exc:
            LOG.warning("Офіційні оголошення недоступні: %s", type(exc).__name__)
            source_problems.append("official_feed")

    if house is not None:
        try:
            from alerts import address_decision
            for _channel, key, value, message in address_decision(
                    house, state, now):
                if key == "address:groups":
                    # Only house_queue_transition owns this state key.
                    continue
                enqueue(4 if COMPACT_DAILY_SCHEDULE else 1, key, value, message, "address")
        except Exception as exc:
            LOG.warning("Адресні події недоступні: %s", type(exc).__name__)
            source_problems.append("address_events")

    try:
        if house_groups is not None:
            source, groups = await get_schedule(house_groups)
        else:
            source, groups = None, []
    except Exception as exc:
        LOG.error("Графік оператор недоступний: %s", type(exc).__name__)
        source_problems.append("provider_schedule")
        source, groups = None, []

    # If an official emergency/GAV regime remains active, the daily GPV
    # cannot be presented as guaranteed operational at this moment.
    emergency_states = {
        key: value.get("status") if isinstance(value, dict) else value
        for key, value in state.items()
        if key.startswith(("official:status:emergency:",
                           "official:status:gav:",
                           "official:incident:emergency:",
                           "official:incident:gav:"))
    }
    for item in pending:
        _priority, _seq, key, value, _message, origin, _deadline, _cursor = item
        if origin == "official" and key.startswith(
                ("official:status:emergency:", "official:status:gav:",
                 "official:incident:emergency:", "official:incident:gav:")):
            emergency_states[key] = value.get("status")
    uncertain_gpv = any(x == "active" for x in emergency_states.values())

    if source:
        # Absence of an unpublished daily table is normal. A PUBLISHED
        # table containing unknown or incomplete slots is not: fail visibly
        # rather than silently treating a broken table as "nothing changed".
        if source.fact and source.preset:
            for stamp, daily_groups in list(source.fact.days.items()):
                try:
                    day = datetime.fromtimestamp(int(stamp), KYIV).date()
                except (TypeError, ValueError, OverflowError, OSError):
                    del source.fact.days[stamp]
                    source_problems.append("invalid_daily_timestamp")
                    continue
                if day not in (today, today + timedelta(days=1)):
                    continue
                for group in groups:
                    if group in daily_groups and parse_verified(
                        daily_groups[group], source.preset.time_zone
                    ) is None:
                        source_problems.append("invalid_daily_fact")
                        LOG.warning("Опублікована добова таблиця пошкоджена")
        for group in groups:
            for key, payload, kind, parts in collect_notifications(
                    source, group, today, state, weekly_mode == "separate"):
                if kind in ("week", "week_cancel") and weekly_mode != "separate":
                    continue
                if weekly_mode == "combined" and kind in ("day", "day_cancel"):
                    continue
                if kind == "week":
                    payload = (state.get("board:weekly_activation"), payload)
                digest = fingerprint(payload)
                old = state.get(key)
                certainty_changed = (
                    kind == "day" and isinstance(old, dict) and state_status(old) == "outages"
                    and bool(old.get("conditional")) != uncertain_gpv
                    and any(mode == "off" for _, _, mode in upcoming_spans(parts[0], parts[1], now))
                )
                if kind != "week" and state_digest(old) == digest and not certainty_changed:
                    continue
                previous_status = state_status(old)
                next_state = {
                    "digest": digest,
                    "status": "cancelled" if kind.endswith("_cancel") else "outages",
                }
                if kind == "day":
                    day, spans, updated = parts
                    old_spans = None
                    if isinstance(old, dict) and isinstance(old.get("spans"), list):
                        old_spans = [tuple(item) for item in old["spans"]]
                    new_visible = upcoming_spans(day, spans, now)
                    old_visible = upcoming_spans(day, old_spans, now)
                    next_state["spans"] = [list(span) for span in spans]
                    next_state["conditional"] = uncertain_gpv

                    # provider only published already-ended slots for today:
                    # do not announce an incident that is over.
                    if day == today and not new_visible and not old_visible:
                        next_state["status"] = "historical_only"
                        state[key] = next_state
                        persist()
                        continue

                    # A previous relevant outage was removed and the new
                    # GPV has nothing left for today: this is useful news.
                    if day == today and not new_visible and old_visible:
                        next_state["status"] = "cancelled"
                        message = cancellation_message(
                            day, group, updated, remaining_today=True
                        )
                        priority = 2
                    else:
                        # provider may edit only an already-ended time range.
                        # That is not a change to the remaining plan.
                        if (day == today and old_spans is not None and
                                upcoming_signature(day, old_spans, now) ==
                                upcoming_signature(day, spans, now) and
                                previous_status == "outages" and not certainty_changed):
                            state[key] = next_state
                            persist()
                            continue

                        event = ("resumed" if previous_status == "cancelled" else
                                 "changed" if previous_status == "outages" else "new")
                        late_now = newly_current_schedule_outage(
                            day, spans, old_spans, now
                        )
                        message = day_message(
                            day, group, new_visible, updated, event=event,
                            previous=old_visible, observed_at=now,
                            total_spans=spans,
                        )
                        priority = 1 if late_now else 2 if event == "changed" else 4
                elif kind == "day_cancel":
                    day, updated = parts
                    old_spans = (
                        [tuple(item) for item in old["spans"]]
                        if isinstance(old, dict) and isinstance(old.get("spans"), list)
                        else None
                    )
                    if day == today and old_spans is not None and not upcoming_spans(
                            day, old_spans, now):
                        next_state["status"] = "cancelled"
                        state[key] = next_state
                        persist()
                        continue
                    message = cancellation_message(
                        day, group, updated, remaining_today=day == today
                    )
                    priority = 2
                elif kind == "week":
                    monday, entries = parts
                    next_state, notify = weekly_notice(
                        entries, state.get("board:weekly_activation"), digest, old, now)
                    if not notify:
                        if next_state != old:
                            state[key] = next_state
                            persist()
                        continue
                    by_day = dict(entries)
                    label = group if len(groups) > 1 else None
                    days = (monday + timedelta(days=n) for n in range(7))
                    message = "\n\n".join(compact_forecast_day(day, by_day.get(day, ()), label, now)
                                           for day in days if day >= today)
                    priority = 7
                elif kind == "week_cancel":
                    message = weekly_cancellation_message(parts[0], group)
                    priority = 3
                else:
                    LOG.warning("Невідомий тип графіка: %s", kind)
                    continue
                if COMPACT_DAILY_SCHEDULE and kind in ("day", "day_cancel"):
                    visible = new_visible if next_state["status"] == "outages" else []
                    if (kind == "day" and not uncertain_gpv and len(groups) == 1
                            and previous_status not in ("outages", "cancelled")
                            and len(visible) == 1 and visible[0][2] == "off"):
                        start, end, _mode = visible[0]
                        midnight = datetime.combine(day, datetime.min.time(), KYIV)
                        interval = (midnight + timedelta(minutes=start), midnight + timedelta(minutes=end))
                        if same_remaining_window(interval, address_window(state.get("address:last_outage")), now):
                            state[key] = next_state
                            persist()
                            continue
                    message = compact_daily_message(
                        day, visible, group=group if len(groups) > 1 else None,
                        conditional=uncertain_gpv,
                    )
                    if not visible and house is not None:
                        from alerts import exact_provider_time
                        start = exact_provider_time(house.start_date)
                        end = exact_provider_time(house.end_date)
                        if start and end and start <= now < end and day == today:
                            end_format = "%H:%M" if end.date() == day else "%d.%m.%Y %H:%M"
                            message += "\nЗа адресою вказано відключення до " + end.strftime(end_format)
                    enqueue(priority, key, next_state, message, "schedule")
                elif message:
                    if house_groups is not None and len(house_groups) > 1:
                        message += (
                            f"\n⚠️ Для будинку {html.escape(operator_label())} вказує кілька черг. "
                            "Цей графік стосується лише зазначеної черги. "
                            "Розподіл квартир між чергами не підтверджено; "
                            f"для будинків зі складним живленням {html.escape(operator_label())} не гарантує прогноз."
                        )
                    if uncertain_gpv and kind in ("day", "week"):
                        message += (
                            f"\n⚠️ {html.escape(operator_label())} повідомляв про аварійний режим на цій території. "
                            "Поки він діє, погодинний графік може не діяти."
                        )
                    enqueue(priority, key, next_state, message, "schedule")

            # Exact lead-time reminders are independent of schedule-change notices.
            if uncertain_gpv:
                continue
            for key, message, deadline in due_reminders(
                    source, group, datetime.now(KYIV), leads, state, parse_verified):
                enqueue(5, key, {"sent": True}, message, "reminder", deadline)

    # Preserve order among official announcements (e.g. start then cancel).
    # A failed send must not advance the official feed checkpoint.
    pending.sort(key=lambda item: (item[0], item[5] == "address", item[1]))
    delivered_schedule_keys = set()
    official_failed = False
    last_send = 0.0
    for _priority, _seq, key, value, message, origin, deadline, cursor in pending:
        if origin == "official" and official_failed:
            continue
        if key == "official:last_id" and origin == "official":
            state[key] = value
            persist()
            continue
        if COMPACT_DAILY_SCHEDULE and origin == "address" and key == "address:last_outage" and message:
            start, _end = address_window(value)
            date_key = max(start, now).date().isoformat() if start else ""
            same_run = bool(house_groups) and all(
                f"{date_key}:{group}" in delivered_schedule_keys for group in house_groups)
            covered = state.get("address:later_schedules", [])
            newer_schedule = bool(house_groups) and all(
                f"{date_key}:{group}" in covered for group in house_groups)
            old = None if same_run or newer_schedule else state.get(key)
            if address_already_announced(value, old, state, house_groups, now, uncertain_gpv):
                message = None
            else:
                message = address_notice_with_schedule(value, now, source, house_groups, uncertain_gpv)
        if not message:
            state[key] = value
            persist()
            continue
        if deadline is not None and datetime.now(KYIV) >= deadline:
            LOG.info("Пропущено прострочене нагадування: %s", _anonymize_state(key))
            continue
        # Limit to one outgoing Telegram message per second for this one chat.
        delay = 1.05 - (time.monotonic() - last_send) if last_send else 0
        if delay > 0:
            time.sleep(delay)
        if deadline is not None and datetime.now(KYIV) >= deadline:
            LOG.info("Пропущено прострочене нагадування: %s", _anonymize_state(key))
            continue
        try:
            if dry_run:
                LOG.info("DRY-RUN: сформовано сповіщення %s; Telegram не викликано", _anonymize_state(key))
            else:
                telegram_send(token, chat, message, deadline=deadline)
        except Exception as exc:
            if origin == "official":
                official_failed = True
            LOG.error("Помилка доставки (%s): %s", origin, type(exc).__name__)
            problems.append(origin)
            continue
        if origin == "schedule":
            delivered_schedule_keys.add(key)
            if key[:10] in (today.isoformat(), (today + timedelta(days=1)).isoformat()):
                covered = [k for k in state.get("address:later_schedules", []) if k[:10] >= today.isoformat()]
                state["address:later_schedules"] = sorted(set(covered + [key]))
        elif origin == "address" and key == "address:last_outage":
            state["address:later_schedules"] = []
        last_send = time.monotonic()
        if origin == "official":
            state["official:last_id"] = max(
                int(state.get("official:last_id", 0)), int(cursor)
            )
            # One record per delivered announcement, not merely a status
            # shared between all incidents of the same regional type.
            state[f"official:event:{cursor}"] = value
        state[key] = value
        persist()
        sent += 1
        LOG.info("%s: %s", "DRY-RUN" if dry_run else "Надіслано", _anonymize_state(key))

    LOG.info("%s: %d; помилок доставки: %d",
             "Сповіщень, які були б надіслані" if dry_run else "Нових сповіщень",
             sent, len(problems))
    pin_enabled = os.getenv("TELEGRAM_SCHEDULE_BOARD") == "true"
    now_enabled = os.getenv("NOW_COMMAND_ENABLED", str(pin_enabled).lower()) == "true"
    menus_enabled = os.getenv("PERSONAL_SUBSCRIPTIONS_ENABLED") == "true"
    if not dry_run and (pin_enabled or now_enabled or menus_enabled):
        from schedule_board import APIError, api, answer_commands, build_board, quiet, update_pin
        configured_chat = str(chat)
        def call(method, **params):
            nonlocal chat
            group_request = str(params.get("chat_id")) in (configured_chat, str(chat))
            if group_request:
                params["chat_id"] = chat
            try:
                return api(token, method, **params)
            except APIError as exc:
                if not (group_request and exc.code == 400 and isinstance(exc.migrated, int) and exc.migrated < 0 and str(exc.migrated) != str(chat)):
                    raise
                chat = str(exc.migrated)
                state["board:migrated_chat"] = chat
                persist()
                LOG.info("Telegram migration confirmed")
                params["chat_id"] = chat
                return api(token, method, **params)
        def send_board(target, text):
            result = call("sendMessage", chat_id=target, text=text, parse_mode="HTML",
                          disable_notification=quiet(datetime.now(KYIV)))
            return result["message_id"]

        owner = token.partition(":")[0]
        if state.get("board:owner") != owner:
            for name in ("board:pin", "board:offset", "board:username"):
                state.pop(name, None)
            state["board:owner"] = owner
        cache = state.setdefault("board:cache", {})
        board = build_board(cache, source, house_groups, now, uncertain_gpv, house,
                            "address_lookup" not in source_problems, parse_verified, compact_daily_message,
                            include_week=weekly_mode == "combined")
        mirror_enabled = os.getenv("UNOFFICIAL_FALLBACK_ENABLED") == "true"
        if ((config.get("official_reserve_url") or mirror_enabled)
                and "provider_schedule" in source_problems and house_groups):
            try:
                from optional_schedules import reserve_view
                from source_registry import reserve_settings
                fallback = await reserve_view(reserve_settings(config), house_groups, now, mirror_enabled)
                if fallback:
                    if "address_lookup" in source_problems:
                        fallback += "\nЧерга будинку за останньою успішною перевіркою."
                    board += "\n\n" + fallback
            except Exception as exc:
                LOG.warning("Резервний графік недоступний: %s", type(exc).__name__)
        persist()
        pin_result = "unknown"
        actions = ([update_pin] if pin_enabled else []) + ([answer_commands] if now_enabled or menus_enabled else [])
        for action in actions:
            try:
                if action is answer_commands and menus_enabled:
                    from optional_schedules import notify_subscriptions, subscription_command, subscription_homes
                    homes = subscription_homes(config, chat)
                    def menu(msg, callback):
                        subscription_command(msg, homes, state, call, send_board, persist, callback)
                    answer_commands(state, board, chat, call, send_board, persist, menu=menu, now_enabled=now_enabled)
                    await notify_subscriptions(homes, state, call, send_board, persist,
                                               config["address"], house_groups if house is not None else None,
                                               source, now, uncertain_gpv, week=weekly_mode)
                else:
                    action(state, board, chat, call, send_board, persist)
                if action is update_pin:
                    pin_result = "ok"
            except RuntimeError as exc:
                LOG.warning("Telegram %s: %s", action.__name__, exc)
                if action is answer_commands:
                    problems.append("now_commands")
                else:
                    pin_result = "failed"
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as report:
                print(f"pin_health={pin_result}", file=report)
    if "address_lookup" in source_problems and os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as report:
            print("address_lookup=failed", file=report)
    if problems:
        raise RuntimeError(f"Помилки Telegram: {len(problems)}; успішні події збережено")
    if source_problems:
        # The workflow must not appear healthy while an enabled official
        # source is silently failing. Valid other notices were processed.
        raise RuntimeError(
            "Неповна перевірка офіційних джерел: " + ", ".join(source_problems)
        )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    single = "--once" in sys.argv
    delay = max(60, int(os.getenv("POLL_INTERVAL_SECONDS", "300")))
    while True:
        try:
            asyncio.run(once())
        except Exception as ex:
            LOG.error("Проверка не выполнена: %s", type(ex).__name__)
            LOG.error("%s", str(ex))
            if single:
                sys.exit(1)
        if single:
            return
        time.sleep(delay)


if __name__ == "__main__":
    main()
