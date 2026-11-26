"""Replay a saved schedule response locally. No address, network or Telegram required."""
import argparse
import json
from datetime import date, datetime
from pathlib import Path

from bot import KYIV, compact_daily_message, parse_verified, time_range
from provider_transport import parse_fact, parse_preset


def replay(document, group, day):
    labels = parse_preset(document["preset"]).time_zone
    ranges = sorted(time_range(label) for label in labels.values())
    if not ranges or ranges[0][0] != 0 or ranges[-1][1] != 1440 or any(
        previous[1] != following[0] for previous, following in zip(ranges, ranges[1:])
    ):
        raise ValueError("Incomplete daily coverage")
    for stamp, rows in parse_fact(document["fact"]).days.items():
        if datetime.fromtimestamp(int(stamp), KYIV).date() == day:
            spans = parse_verified(rows.get(group), labels)
            if spans is not None:
                if not spans:
                    return day.strftime("%d.%m.%Y") + "\nВідключень за графіком немає"
                return compact_daily_message(day, spans)
    raise ValueError("No complete schedule for this date and queue")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("group")
    parser.add_argument("day", type=date.fromisoformat)
    args = parser.parse_args()
    print(replay(json.loads(args.file.read_text()), args.group, args.day))
