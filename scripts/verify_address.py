"""Read-only private address and source verification; never prints the address."""
import asyncio
import logging
import os
import sys

from power_config import provider_config, address_json
from event_scope import house_scope_from_secret
from alerts import fetch_address_status
from datetime import datetime
from bot import KYIV, confirm_address_groups, get_schedule


async def main():
    provider_config()
    scope = house_scope_from_secret(address_json())
    print("Address configured:", True)
    print("District configured:", bool(scope.get("district")))
    print("Quarter configured:", bool(scope.get("quarter")))
    response = await fetch_address_status(address_json())
    groups, source = await confirm_address_groups(response.group_ids, datetime.now(KYIV))
    print("Verified queue count:", len(groups))
    _, available = await get_schedule(groups, source=source)
    print("Official schedule accessible for:", len(available), "queues")
    print("No Telegram messages were sent.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(main())
    except Exception as exc:
        print("Source verification failed:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)
