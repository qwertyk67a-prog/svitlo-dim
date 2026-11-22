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


def collect_notifications(source, group, today, state=None):
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
    plan = source.preset.groups.get(group) if source.preset.is_active else None
    entries = []
    all_seven_verified = bool(plan)
    if plan:
        for weekday in range(1, 8):
            item = plan.get_day(weekday)
            spans = parse_verified(item.slots, labels) if item else None
            if spans is None:
                all_seven_verified = False
                break
            if spans:
                entries.append((
                    monday + timedelta(days=weekday - 1),
                    tuple((a, b) for a, b, _mode in spans),
                ))

    if all_seven_verified and entries:
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
    parts = key.split(":")
    if (len(parts) >= 3 and parts[0] in ("week", "reminder")
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1])):
        index = 2
    elif len(parts) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
        index = 1
    else:
        return key
    if parts[index] not in mapping:
        return key
    parts[index] = mapping[parts[index]]
    return ":".join(parts)


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
    for attempt in range(3):
        if deadline is not None and datetime.now(KYIV) >= deadline:
            raise RuntimeError("Термін нагадування минув; застаріле повідомлення не надсилається")
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": "true"},
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

    def persist():
        """Never mutate a real notification checkpoint during a dry run."""
        if not dry_run:
            state_write(path, state)

    now = datetime.now(KYIV)
    today = now.date()
    leads = parse_reminder_minutes(os.getenv("REMINDER_MINUTES", "0"))
    trim_state(state, today)
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
            if state.get("house:last_verified_at") != verified_hour:
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
            cached = state.get("house:groups")
            if cached and cached_queue_is_fresh(
                    state.get("house:last_verified_at"), now):
                try:
                    house_groups = verified_house_queues(cached)
                    house_snapshot = list(house_groups)
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
                enqueue(1, key, value, message, "address")
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
            for stamp, daily_groups in source.fact.days.items():
                try:
                    day = datetime.fromtimestamp(int(stamp), KYIV).date()
                except (TypeError, ValueError, OverflowError):
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
            for key, payload, kind, parts in collect_notifications(source, group, today, state):
                digest = fingerprint(payload)
                old = state.get(key)
                if state_digest(old) == digest:
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
                                previous_status == "outages"):
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
                    confirmed_days = set()
                    if source.fact:
                        for stamp, group_map in source.fact.days.items():
                            confirmed_day = datetime.fromtimestamp(
                                int(stamp), KYIV
                            ).date()
                            slots = group_map.get(group)
                            if parse_verified(
                                slots, source.preset.time_zone
                            ) is not None:
                                confirmed_days.add(confirmed_day)
                    message = weekly_message(
                        monday, group, entries,
                        changed=previous_status == "outages",
                        observed_at=now,
                        confirmed_days=confirmed_days,
                    )
                    priority = 7
                elif kind == "week_cancel":
                    message = weekly_cancellation_message(parts[0], group)
                    priority = 3
                else:
                    LOG.warning("Невідомий тип графіка: %s", kind)
                    continue
                if message:
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
            for key, message, deadline in due_reminders(
                    source, group, datetime.now(KYIV), leads, state, parse_verified):
                enqueue(5, key, {"sent": True}, message, "reminder", deadline)

    # Preserve order among official announcements (e.g. start then cancel).
    # A failed send must not advance the official feed checkpoint.
    pending.sort(key=lambda item: (item[0], item[1]))
    official_failed = False
    last_send = 0.0
    for _priority, _seq, key, value, message, origin, deadline, cursor in pending:
        if origin == "official" and official_failed:
            continue
        if key == "official:last_id" and origin == "official":
            state[key] = value
            persist()
            continue
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
