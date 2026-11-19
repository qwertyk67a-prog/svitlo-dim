"""Official DTEK event notices. No address, token or private chat IDs are stored here.

The public DTEK announcement stream is only a REGIONAL indicator.
House-specific outages require an optional encrypted DTEK_ADDRESS_JSON secret.
"""
import hashlib
import html
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("dtek-alerts")
KYIV = ZoneInfo("Europe/Kyiv")
OFFICIAL_URL = "https://t.me/s/dtek_ua"
DTEK_AJAX = "https://www.dtek-dnem.com.ua/ua/ajax"
DTEK_SOURCE = "https://www.dtek-dnem.com.ua/ua/shutdowns"


def official_posts(page):
    soup = BeautifulSoup(page, "html.parser")
    entries = []
    for post in soup.select(".tgme_widget_message[data-post]"):
        identity = post.get("data-post", "")
        match = re.fullmatch(r"dtek_ua/(\d+)", identity)
        if not match:
            continue
        content = post.select_one(".tgme_widget_message_text")
        clock = post.select_one("time[datetime]")
        if content is None or clock is None:
            continue
        try:
            stamp = datetime.fromisoformat(clock["datetime"])
        except (ValueError, KeyError):
            continue
        if stamp.tzinfo is None:
            continue
        entries.append({
            "id": int(match.group(1)),
            "date": stamp.astimezone(KYIV),
            "text": content.get_text("\n", strip=True),
            "url": f"https://t.me/dtek_ua/{match.group(1)}",
        })
    return sorted(entries, key=lambda p: p["id"])


def regional_event(post):
    """Only direct operational announcements from the official DTEK channel.

    A regional headline is insufficient if the text identifies another town.
    Territorial relevance is checked independently by coverage_for_house().
    """
    lines = post["text"].strip().splitlines()
    if not lines:
        return None
    title = re.sub(r"^[^А-Яа-яІіЇїЄєҐґ]+", "", lines[0]).strip().casefold()
    target = re.match(
        r"^(дніпропетровщина|дніпропетровська область|кривий ріг|"
        r"україна|укренерго|дтек|"
        r"[а-яіїєґ][а-яіїєґ\- ]+ район|[0-9 ]+ квартал)\s*:",
        title,
    )
    # Official DTEK also publishes direct headlines starting with the
    # Ukrenergo order rather than an area prefix.
    order = re.match(
        r"^за\s+(?:командою|наказом|розпорядженням)\s+"
        r"(?:нек\s+)?укренерго\b", title,
    )
    if not target and not order:
        return None
    context = " ".join(lines[:3]).casefold()[:650]
    if order and not re.search(
        r"дніпропетровськ|дніпропетровщ|крив\w*\s+р\w*|"
        r"по\s+всій\s+україн\w*|в\s+усіх\s+областях",
        context,
    ):
        return None
    restore_gpv = bool(re.search(
        r"(?:переходимо|повертаємось|повертаємося|перейшли|повернулися)\s+"
        r"(?:до|на)\s+(?:стабілізаційн\w*\s+)?графік\w*|"
        r"(?:відновлен\w*|відновили)\s+(?:дію\s+)?стабілізаційн\w*\s+графік\w*",
        context,
    ))
    if restore_gpv:
        kind = "emergency"
    elif re.search(r"\b(?:гав|графік\w*\s+аварійн\w*\s+відключен\w*)\b", context):
        kind = "gav"
    elif "екстрен" in context and "відключен" in context:
        kind = "emergency"
    elif re.search(
        r"\b(?:знеструмл\w*|без\s+світла|"
        r"(?:відсутн\w*|припинен\w*|відновлен\w*|відновл\w*)\s+"
        r"електропостачан\w*|заживлен\w*)\b",
        context,
    ):
        kind = "area"
    else:
        return None
    if restore_gpv or re.search(
        r"скасован|завершено|завершені|закінчен|відмінено|"
        r"відновлено|відновили|заживлено|заживили|повернул\w*\s+світло",
        title,
    ):
        status = "ended"
    elif re.search(
        r"запроваджен|застосован|введен|діють|тривають|продовжен|"
        r"знеструмл|без\s+світла|відсутн\w*\s+електропостачан",
        context,
    ):
        status = "active"
    else:
        return None
    area = target.group(1) if target else ""
    if area == "кривий ріг" or (order and "крив" in context):
        area_label = "Кривий Ріг"
    elif area in ("дніпропетровщина", "дніпропетровська область") or (
        order and re.search(r"дніпропетровськ|дніпропетровщ", context)
    ):
        area_label = "Дніпропетровська область"
    elif area in ("україна", "укренерго") or order:
        area_label = "Україна"
    else:
        area_label = area.capitalize() if area else "ДТЕК"
    return {
        "kind": kind, "status": status, "area": area_label,
        "post": post, "operator": "ДТЕК", "source": post["url"],
    }

def regional_message(event, changed=False):
    kind = event["kind"]
    active = event["status"] == "active"
    area = event["area"]
    if kind == "emergency":
        title = ("‼️ <b>Запроваджено екстрені відключення</b>" if active else
                 "✅ <b>Екстрені відключення скасовано</b>")
        body = ("Під час екстрених відключень погодинний графік може не діяти." if active
                else "Скасовано саме екстрений режим. Актуальний погодинний графік перевіряється окремо.")
    elif kind == "gav":
        title = ("‼️ <b>Оголошено графіки аварійних відключень</b>" if active else
                 "✅ <b>Графіки аварійних відключень скасовано</b>")
        body = ("Погодинний графік може бути неактуальним." if active else
                "Оновлення стосується аварійного режиму, а не гарантії наявності світла.")
    else:
        title = ("⚠️ <b>ДТЕК повідомив про знеструмлення</b>" if active else
                 "✅ <b>ДТЕК повідомив про відновлення електропостачання на території</b>")
        body = ("Відключення можуть відбуватися поза погодинним графіком." if active else
                "Це повідомлення про територію, а не підтвердження відновлення у будинку.")

    if changed == "publication" and active:
        title = "🔄 <b>ДТЕК змінив офіційне повідомлення</b>"
        body = "Змінено текст першоджерела. Час відключення міг залишитися незмінним."
    elif changed and active:
        title = "🔄 <b>ДТЕК уточнив строки відключення</b>"
        body = "Офіційні дані про час події оновлено."
    coverage = event.get("coverage") or {}
    label = html.escape(coverage.get("label") or area)
    place_note = (
        f"Зазначена територія: {label}. "
        "Будинок розташований у цій території."
        if coverage.get("level") != "house" else
        "ДТЕК прямо зазначив цей будинок."
    )
    if not active:
        uncertainty = (
            "За повідомленням ДТЕК, попередній режим для території завершено. "
            "Це не підтверджує наявність електроенергії у кожному будинку."
        )
    elif coverage.get("level") == "house":
        uncertainty = (
            "Подія зазначена для будинку за адресою. "
            "Бот не вимірює фактичну наявність електроенергії."
        )
    elif coverage.get("partial"):
        uncertainty = (
            "ДТЕК повідомляє про часткове знеструмлення на території. "
            "Невідомо, чи зачепило відключення конкретний будинок."
        )
    else:
        uncertainty = (
            "Інформація стосується території, до якої входить будинок. "
            "Фактичну наявність електроенергії у будинку не підтверджено."
        )
    reported = event["post"]["date"].astimezone(KYIV).strftime("%d.%m.%Y %H:%M")
    started = published_event_start(event["post"])
    ending = published_event_end(event["post"])
    times = []
    if started:
        times.append("Зазначений початок: " + started.strftime("%d.%m.%Y %H:%M"))
    if ending:
        times.append("Орієнтовне завершення: " + ending.strftime("%d.%m.%Y %H:%M"))
    return "\n".join((
        title, f"📍 {html.escape(area)}", "",
        place_note, uncertainty,
        f"Офіційне повідомлення опубліковано: {reported}.",
        *times, body,
        "Це повідомлення не підтверджує відключення кожної квартири.",
        f'<a href="{html.escape(event["post"]["url"], quote=True)}">Офіційне повідомлення ДТЕК</a>',
    ))


def published_event_end(post):
    """Only trust an explicitly dated end timestamp in an official post.

    An undated "до 18:00" is ambiguous across days; do not infer it.
    """
    text = post["text"].casefold()
    raw = re.search(
        r"(?:орієнтовн\w*\s+(?:завершенн\w*|відновленн\w*)|"
        r"завершенн\w*|відновленн\w*|до)\s*[:—–\-]?\s*"
        r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}|"
        r"\d{2}:\d{2}\s+\d{2}\.\d{2}\.\d{4})",
        text,
    )
    return parse_dtek_date(raw.group(1)) if raw else None


def published_event_start(post):
    """Return a start only when DTEK supplied a complete date and time."""
    text = post["text"].casefold()
    match = re.search(
        r"(?:початок|розпочат\w*|з\s+)\s*[:—–\-]?\s*"
        r"(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}|"
        r"\d{2}:\d{2}\s+\d{2}\.\d{2}\.\d{4})",
        text,
    )
    return parse_dtek_date(match.group(1)) if match else None


def event_record(event, now):
    """Public deduplication state contains no private address or chat ID."""
    post = event["post"]
    start = published_event_start(post)
    finish = published_event_end(post)
    level = event["coverage"]["level"]
    # A date is an identity fallback, NOT a claimed outage start time.
    identity = (event["kind"], event["area"], level,
                start.isoformat() if start else post["date"].date().isoformat())
    revision = (event["kind"], event["area"], level,
                start.isoformat() if start else None,
                finish.isoformat() if finish else None)
    fingerprint = lambda value: hashlib.sha256(
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "status": event["status"],
        "event_id": fingerprint(identity),
        "post_id": post["id"],
        "content_hash": event.get("content_hash"),
        "revision": fingerprint(revision),
        "operator": "ДТЕК",
        "source": post["url"],
        "published": post["date"].isoformat(),
        "detected": now.isoformat(),
        "start": start.isoformat() if start else None,
        "end": finish.isoformat() if finish else None,
        "area": event["area"],
    }


def region_candidates(page, state, now, scope=None):
    """Evaluate separately published DTEK events and previously edited posts.

    Each new significant announcement is examined in publication order.
    A cancellation already visible in the same batch suppresses obsolete
    initial alerts. All content-hash and cursor updates are deferred until
    successful notification delivery.
    """
    posts = official_posts(page)
    if not posts:
        raise RuntimeError("Не вдалося прочитати офіційний канал ДТЕК")
    from event_scope import coverage_for_house

    old_id = state.get("official:last_id")
    first_run = old_id is None
    if first_run:
        old_id = min(p["id"] for p in posts) - 1
    try:
        old_id = int(old_id)
    except (ValueError, TypeError):
        raise RuntimeError("Некоректний стан останнього офіційного повідомлення")

    old_hashes = state.get("official:post_hashes", {})
    if not isinstance(old_hashes, dict):
        old_hashes = {}
    hashes = dict(old_hashes)
    max_id = max(p["id"] for p in posts)
    by_scope = {}
    for post in posts:
        pid = str(post["id"])
        digest = hashlib.sha256(
            (post["date"].isoformat() + "\n" + post["text"]).encode("utf-8")
        ).hexdigest()[:24]
        edit = pid in old_hashes and old_hashes[pid] != digest
        new = post["id"] > old_id
        hashes[pid] = digest
        if not (new or edit):
            continue
        if post["date"] > now + timedelta(minutes=10):
            continue
        event = regional_event(post)
        coverage = coverage_for_house(post["text"], scope) if event else None
        if not event or not coverage:
            continue
        event["coverage"] = coverage
        event["content_hash"] = digest
        age = now - post["date"]
        finish = published_event_end(post)
        if age > timedelta(days=2) and not (
            finish and finish > now and age <= timedelta(days=7)
        ):
            continue
        if event["status"] == "active" and finish and finish <= now:
            event["status"] = "expired"
        key = f"official:status:{event['kind']}:{coverage['level']}"
        by_scope.setdefault(key, []).append((post["id"], event))

    pending = []
    for key, candidates in by_scope.items():
        previous = state.get(key)
        previous_status = (
            previous.get("status") if isinstance(previous, dict) else previous
        )
        # Do not announce an outage and its resolution after BOTH happened.
        # With a previously announced outage, send the final closure.
        last_id, last_event = candidates[-1]
        if last_event["status"] in ("ended", "expired"):
            if previous_status == "active":
                record = event_record(last_event, now)
                text = (regional_message(last_event)
                        if last_event["status"] == "ended" else None)
                pending.append((last_id, key, record, text))
            continue

        shadow = previous
        for post_id, event in candidates:
            current = event_record(event, now)
            status = current["status"]
            prior_status = (
                shadow.get("status") if isinstance(shadow, dict) else shadow
            )
            if status in ("ended", "expired"):
                if prior_status == "active":
                    pending.append((
                        post_id, key, current,
                        regional_message(event) if status == "ended" else None,
                    ))
                    shadow = current
                continue

            if prior_status != "active":
                pending.append((post_id, key, current, regional_message(event)))
                shadow = current
                continue

            old_start = shadow.get("start") if isinstance(shadow, dict) else None
            old_end = shadow.get("end") if isinstance(shadow, dict) else None
            # Missing official timestamps are NOT evidence that an estimate
            # changed or was cancelled.
            updated_time = (
                bool(current["start"] and current["start"] != old_start)
                or bool(current["end"] and current["end"] != old_end)
            )
            title = event["post"]["text"].splitlines()[0].casefold()
            fresh_activation = bool(
                re.search(r"запроваджен|введен|розпочат", title)
                and isinstance(shadow, dict)
                and current["content_hash"] != shadow.get("content_hash")
                and current["source"] != shadow.get("source")
            )
            edited_post = bool(
                isinstance(shadow, dict)
                and current["source"] == shadow.get("source")
                and current["content_hash"] != shadow.get("content_hash")
            )
            if updated_time or fresh_activation or edited_post:
                pending.append((
                    post_id, key, current,
                    regional_message(
                        event, changed="publication"
                        if edited_post and not updated_time else True
                    ),
                ))
                shadow = current

    # A content hash stores a publication version, never private house data.
    # Keep the most recent 100 visible posts, not an unbounded public history.
    visible_keys = [k for k in hashes if str(k).isdigit()]
    limited_hashes = {
        k: hashes[k] for k in
        sorted(visible_keys, key=lambda x: int(x))[-100:]
    }
    if first_run and not pending:
        state["official:last_id"] = max_id
        state["official:post_hashes"] = limited_hashes
        LOG.info("Початкова синхронізація офіційного каналу: %d", max_id)
        return []
    # Cursor and hash snapshots commit only after successful sends.
    pending.sort(key=lambda item: item[0])
    pending.append((max(old_id, max_id), "official:post_hashes",
                    limited_hashes, None))
    pending.append((max(old_id, max_id), "official:last_id",
                    max(old_id, max_id), None))
    return pending


def fetch_official_page():
    response = requests.get(
        OFFICIAL_URL, timeout=22,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SVITLO/1.0)"},
    )
    response.raise_for_status()
    return response.text


def normalized_house(value):
    """Preserve house-number separators: 1/1 is NOT house 11."""
    text = re.sub(r"\s+", "", str(value)).casefold().replace("\\", "/")
    text = text.replace("a", "а").replace("b", "в").replace("i", "і")
    # DTEK commonly spells a letter suffix with or without a dash.
    return re.sub(r"(?<=\d)-(?=[а-яіїєґ])", "", text)


def address_fields(secret):
    """Validate private JSON; never put its value into logs."""
    if not secret:
        return None
    try:
        raw = json.loads(secret)
    except (ValueError, TypeError):
        raise RuntimeError("Некоректний формат DTEK_ADDRESS_JSON")
    if not isinstance(raw, dict):
        raise RuntimeError("DTEK_ADDRESS_JSON повинен містити об'єкт")
    fields = [raw.get(k) for k in ("city", "street", "house")]
    if any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in fields):
        raise RuntimeError("DTEK_ADDRESS_JSON: обов'язкові поля city/street/house")
    return fields


def get_house_entry(response, house):
    matches = [(key, e) for key, e in response.houses.items()
               if normalized_house(key) == normalized_house(house)]
    if len(matches) != 1:
        # Never expose configured address or candidate numbers in logs.
        # Loose matching alone would conflate 11 and 1/1.
        def rough(value):
            return re.sub(r"[\\/\s.\-]", "", normalized_house(value))
        similar_count = sum(
            rough(key) == rough(house) for key in response.houses
        )
        LOG.warning(
            "Адресний довідник ДТЕК: записів=%d, точних збігів=%d, "
            "лише приблизних збігів=%d",
            len(response.houses), len(matches), similar_count,
        )
        raise RuntimeError("Будинок не знайдено однозначно в ДТЕК")
    entry = matches[0][1]
    # An empty record can mean none; but only in a successfully parsed response.
    return entry


async def fetch_address_status(secret):
    """Use private address only for DTEK address lookup, never regional schedule."""
    from curl_cffi.requests import AsyncSession
    from dtek_client import DtekClient
    from dtek_client.browser_auth import get_cleared_cookies

    city, street, house = address_fields(secret)

    async def read(client):
        response = await client.get_home_num(city, street)
        return get_house_entry(response, house)

    try:
        async with DtekClient("dnem", ajax_url=DTEK_AJAX) as client:
            return await read(client)
    except Exception as ex:
        LOG.warning("Прямий адресний запит відхилено (%s). Пробую браузер.",
                    type(ex).__name__)
        cookies, csrf = await get_cleared_cookies(DTEK_SOURCE)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://www.dtek-dnem.com.ua",
            "Referer": DTEK_SOURCE,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        if csrf:
            headers["X-CSRF-Token"] = csrf
        session = AsyncSession(timeout=20, cookies=cookies, headers=headers,
                               impersonate="chrome120")
        async with DtekClient("dnem", ajax_url=DTEK_AJAX, session=session) as client:
            return await read(client)


def parse_dtek_date(raw):
    if not raw:
        return None
    for pattern in ("%d.%m.%Y %H:%M", "%H:%M %d.%m.%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(raw), pattern).replace(tzinfo=KYIV)
        except ValueError:
            pass
    return None


def address_event(entry):
    reason = str(entry.sub_type or "").strip()
    beginning = str(entry.start_date or "").strip()
    ending = str(entry.end_date or "").strip()
    outage_type = str(entry.outage_type or "")
    if not (reason or beginning or ending):
        return None
    if outage_type == "1" or "планов" in reason.casefold() or "ремонт" in reason.casefold():
        category = "repair"
    elif "авар" in reason.casefold() or "пошкодж" in reason.casefold():
        category = "fault"
    else:
        category = "unclassified"
    return {
        "category": category,
        "reason": reason,
        "start": beginning,
        "end": ending,
    }


def address_message(event, changed=False, now=None):
    """Differentiate an already-started outage from a future announcement.

    The official address entry does not provide actual voltage telemetry.
    The detection time is NOT asserted to be the publication time.
    """
    titles = {
        "repair": "🛠 <b>Планові ремонтні роботи</b>",
        "fault": "‼️ <b>Аварійне відключення за даними ДТЕК</b>",
        "unclassified": "⚠️ <b>Повідомлення ДТЕК про відключення</b>",
    }
    title = ("🔄 <b>Оновлено дані щодо відключення</b>" if changed
             else titles[event["category"]])
    lines = [title]
    start = parse_dtek_date(event["start"])
    finish = parse_dtek_date(event["end"])
    # A date without a clock time does not establish an exact start moment.
    has_exact_start = bool(re.fullmatch(
        r"(?:\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}|\d{2}:\d{2} \d{2}\.\d{2}\.\d{4})", event["start"]
    ))
    already_started = bool(now and has_exact_start and start and
                           start < now and (not finish or finish > now))
    if already_started:
        lines.append(
            "⚠️ За даними ДТЕК, зазначений час початку вже минув. "
            "Відключення могло початися до отримання цього повідомлення."
        )
    if event["start"]:
        lines.append("Зазначений початок: " + html.escape(event["start"]))
    if event["end"]:
        lines.append("Орієнтовне завершення: " + html.escape(event["end"]))
        lines.append("Час відновлення може змінитися.")
    else:
        lines.append("Орієнтовний час відновлення не зазначено.")
    if event["category"] == "repair":
        lines.append("Причина: планові роботи (за даними ДТЕК).")
    elif event["category"] == "fault":
        lines.append("Причина за ДТЕК: аварійне відключення.")
    if now:
        lines.append("Інформацію виявлено: " +
                     now.astimezone(KYIV).strftime("%d.%m.%Y %H:%M") + ".")
    lines.append("Бот не вимірює фактичну наявність електроенергії.")
    lines.append(f'<a href="{DTEK_SOURCE}">Джерело: ДТЕК</a>')
    return "\n".join(lines)


def address_clear_message(old, now):
    # Store only the end time publicly. Never persist the private reason
    # or the original address response in bot_state.json.
    end = parse_dtek_date(old.get("end"))
    if end is None or now >= end:
        return None  # Normal disappearance after predicted end: no extra noise.
    return "\n".join((
        "✅ <b>Інформацію про позаграфікове відключення знято</b>",
        "Раніше оголошене відключення більше не відображається за адресою.",
        "Це не підтверджує фактичну наявність світла.",
        f'<a href="{DTEK_SOURCE}">Джерело: ДТЕК</a>',
    ))


def address_decision(entry, state, now, expected="GPV6.2"):
    """One state transition per meaningful verified address change."""
    result = []
    if not entry:
        return result
    key = "address:last_outage"
    old = state.get(key)
    event = address_event(entry)
    old_active = isinstance(old, dict) and old.get("status") == "active"
    old_end = parse_dtek_date(old.get("end")) if old_active else None
    # A date without a clock time is NOT evidence of an ended event.
    exact_end = bool(event and re.fullmatch(
        r"(?:\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}|"
        r"\d{2}:\d{2} \d{2}\.\d{2}\.\d{4})", event["end"]
    ))
    finish = parse_dtek_date(event["end"]) if exact_end else None
    if event and finish and finish <= now:
        if old_active and old_end and old_end > now:
            # DTEK explicitly revised the estimate to a time already past.
            # This is an early schedule correction, not voltage telemetry.
            message = "\n".join((
                "🔄 <b>ДТЕК уточнив орієнтовний час завершення</b>",
                "Нове орієнтовне завершення: " + html.escape(event["end"]),
                "Зазначений час уже минув.",
                "Фактичне відновлення електроенергії в будинку не підтверджено.",
                f'<a href="{DTEK_SOURCE}">Джерело: ДТЕК</a>',
            ))
            result.append(("address", key, {
                "status": "expired", "end": event["end"],
            }, message))
        elif old_active:
            result.append(("address", key, {
                "status": "expired", "end": event["end"],
            }, None))
        event = None
    if event:
        fingerprint = {k: event[k] for k in ("category", "start", "end")}
        digest = hashlib.sha256(json.dumps(
            fingerprint, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        if not isinstance(old, dict) or old.get("digest") != digest:
            result.append(("address", key, {
                "digest": digest,
                "end": event["end"],
                "status": "active",
            }, address_message(event, changed=old_active, now=now)))
    elif not exact_end and old_active and old_end and old_end <= now:
        # Disappearance from the address API is NOT proof of cancellation.
        # Expire an elapsed estimate silently, without claiming restoration.
        result.append(("address", key, {
            "status": "expired", "end": old.get("end"),
        }, None))
    # Group changes need human attention. Do not broadcast initial group assignment.
    previous = state.get("address:groups")
    current = sorted(set(entry.group_ids))
    if (previous is not None and previous != current) or (
        previous is None and current and expected not in current
    ):
        label = ", ".join(x.removeprefix("GPV") for x in current)
        text = (
            "⚠️ <b>ДТЕК змінив чергу за адресою</b>\n"
            f"Наразі вказані черги: {html.escape(label or 'не визначено')}.\n"
            "Перевірте актуальність автоматичних повідомлень за чергою 6.2.\n"
            f'<a href="{DTEK_SOURCE}">Джерело: ДТЕК</a>'
        )
        result.append(("address", "address:groups", current, text))
    if previous is None:
        result.append(("address", "address:groups", current, None))
    return result
