"""Manual, read-only validation of DTEK house lookup.

Uses DTEK_ADDRESS_JSON exclusively from GitHub Actions Secret.
Never prints the address, token, raw DTEK response or private identifiers.
Does not send Telegram messages or change notification state.
"""
import asyncio
import logging
import os
import sys
import re

from event_scope import house_scope_from_secret

from alerts import fetch_address_status
from bot import get_schedule
from house_reliability import verified_house_queues


async def main():
    if not os.getenv("DTEK_ADDRESS_JSON", "").strip():
        raise RuntimeError("Repository Secret DTEK_ADDRESS_JSON не задано")
    scope = house_scope_from_secret(os.environ["DTEK_ADDRESS_JSON"])
    district = scope.get("district", "").strip().casefold()
    quarter = scope.get("quarter", "").strip().casefold()
    # These checks never print the stored secret or the real street/house.
    district_ok = bool(re.fullmatch(
        r"покровськ(?:ий|ого|ому)\s+район", district
    ))
    quarter_ok = bool(re.fullmatch(
        r"129\s*[- ]?\s*квартал", quarter
    ))
    print("Перевірка збереженої адреси: район =", "OK" if district_ok else "НЕ ЗБІГАЄТЬСЯ")
    print("Перевірка збереженої адреси: квартал =", "OK" if quarter_ok else "НЕ ЗБІГАЄТЬСЯ")
    if not (district_ok and quarter_ok):
        raise RuntimeError("Район або квартал не збігається з очікуваним")
    entry = await fetch_address_status(os.environ["DTEK_ADDRESS_JSON"])
    groups = verified_house_queues(entry.group_ids)
    print("ДТЕК підтвердив кількість черг будинку:", len(groups))
    print("Підтверджені черги:", ", ".join(x.removeprefix("GPV") for x in groups))
    _source, available = await get_schedule(groups)
    print("Регіональний розклад доступний для", len(available), "черг")
    print("Перевірка завершена. Повідомлення Telegram НЕ надсилалися.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(main())
    except Exception as exc:
        print("Помилка безпечної перевірки:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)
