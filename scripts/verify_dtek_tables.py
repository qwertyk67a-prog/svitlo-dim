"""Read-only comparison of official DTEK daily fact and weekly preset.

Does not read private addresses, Telegram tokens or notification state.
Reads both tables in the SAME official DTEK checkDisconUpdate response.
"""
import asyncio
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from bot import get_schedule, parse_verified, clock

KYIV = ZoneInfo("Europe/Kyiv")
TARGET_DAY = os.getenv("DTEK_COMPARE_DATE", "2026-11-20")
QUEUE = "GPV6.2"


def spans_as_data(spans):
    if spans is None:
        return None
    return [{"start": clock(a), "end": clock(b), "type": kind}
            for a, b, kind in spans]


async def main():
    day = datetime.strptime(TARGET_DAY, "%Y-%m-%d").date()
    fetched_at = datetime.now(KYIV).isoformat(timespec="seconds")
    source, groups = await get_schedule((QUEUE,))
    labels = source.preset.time_zone
    if not labels:
        raise RuntimeError("DTEK did not provide time slot labels")
    daily = None
    if source.fact:
        for stamp, group_map in source.fact.days.items():
            local_day = datetime.fromtimestamp(int(stamp), KYIV).date()
            if local_day == day:
                daily = parse_verified(group_map.get(QUEUE), labels)
                break
    plan = source.preset.groups.get(QUEUE)
    preset = None
    if source.preset.is_active and plan:
        weekday = plan.get_day(day.isoweekday())
        preset = parse_verified(weekday.slots, labels) if weekday else None
    result = {
        "operator": "DTEK Dniprovski Elektromerezhi",
        "official_url": "https://www.dtek-dnem.com.ua/ua/shutdowns",
        "source_api": "https://www.dtek-dnem.com.ua/ua/ajax",
        "queue": QUEUE,
        "target_date": day.isoformat(),
        "fetched_at_kyiv": fetched_at,
        "daily_fact_updated_at": str(getattr(source.fact, "update", None)),
        "daily_fact": spans_as_data(daily),
        "weekly_preset_active": bool(source.preset.is_active),
        "weekly_preset": spans_as_data(preset),
        "message": (
            "Daily fact is more specific; preset is a preliminary weekly forecast."
        ),
    }
    print("OFFICIAL_DTEK_COMPARISON:")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if daily is None or preset is None:
        raise RuntimeError("Unable to verify both DTEK tables for target day")


if __name__ == "__main__":
    asyncio.run(main())
