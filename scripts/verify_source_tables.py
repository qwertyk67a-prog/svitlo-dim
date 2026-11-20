"""Read-only comparison of daily actual schedules and preliminary weekly plan."""
import asyncio
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from bot import get_schedule, parse_verified, clock
from alerts import fetch_address_status
from power_config import address_json

KYIV = ZoneInfo("Europe/Kyiv")


def as_data(spans):
    if spans is None:
        return None
    return [{"start": clock(a), "end": clock(b), "type": kind}
            for a, b, kind in spans]


async def main():
    now = datetime.now(KYIV)
    day = datetime.strptime(
        os.getenv("COMPARE_DATE", now.date().isoformat()), "%Y-%m-%d"
    ).date()
    home = await fetch_address_status(address_json())
    if not home or not home.group_ids:
        raise RuntimeError("No verified address queues")
    source, groups = await get_schedule(home.group_ids)
    labels = source.preset.time_zone
    if not labels:
        raise RuntimeError("Provider gave no time labels")
    for queue in groups:
        daily = None
        if source.fact:
            for stamp, group_map in source.fact.days.items():
                if datetime.fromtimestamp(int(stamp), KYIV).date() == day:
                    daily = parse_verified(group_map.get(queue), labels)
                    break
        preset = None
        plan = source.preset.groups.get(queue)
        if source.preset.is_active and plan:
            weekday = plan.get_day(day.isoweekday())
            preset = parse_verified(weekday.slots, labels) if weekday else None
        print("SOURCE_COMPARISON:")
        print(json.dumps({
            "queue": queue, "target_date": day.isoformat(),
            "daily_fact_updated_at": str(getattr(source.fact, "update", None)),
            "daily_fact": as_data(daily),
            "weekly_preset_active": bool(source.preset.is_active),
            "weekly_preset": as_data(preset),
        }, ensure_ascii=False, sort_keys=True, indent=2))
        if daily is None or preset is None:
            raise RuntimeError("Unable to compare both tables for this queue")


if __name__ == "__main__":
    asyncio.run(main())
