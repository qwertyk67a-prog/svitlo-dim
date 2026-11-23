"""Read two official JSON feeds with field paths selected by the installation."""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


def field(document, path):
    value = document
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError("Configured provider field is absent")
        value = value[key]
    return value


def rows(document, path):
    values = field(document, path)
    if not isinstance(values, list) or not all(isinstance(v, dict) for v in values):
        raise ValueError("Expected an array of provider records")
    return values


def homes(document, mapping, city, street):
    result = {}
    for row in rows(document, mapping["addresses"]):
        if (field(row, mapping["city"]), field(row, mapping["street"])) != (city, street):
            continue
        number = field(row, mapping["house"])
        groups = field(row, mapping["queues"])
        if isinstance(groups, str):
            groups = [groups]
        if (type(number) not in (str, int) or not str(number).strip()
                or not isinstance(groups, list) or not groups
                or not all(isinstance(q, str) and q.strip() for q in groups)):
            raise ValueError("Invalid provider address or queue")
        record = {"sub_type_reason": groups}
        for key in ("sub_type", "start_date", "end_date", "type"):
            if key in mapping:
                record[key] = field(row, mapping[key])
        result.setdefault(str(number), []).append(record)
    return {"data": result}


def translate_slots(slots, labels, values):
    if not isinstance(slots, dict) or set(slots) != set(labels):
        raise ValueError("Incomplete provider schedule")
    if any(not isinstance(v, str) or v not in values for v in slots.values()):
        raise ValueError("Unknown provider status")
    return {k: values[v] for k, v in slots.items()}


def schedule(document, mapping):
    labels = field(document, mapping["labels"])
    if not isinstance(labels, dict) or not labels:
        raise ValueError("Missing official time labels")
    days = {}
    for row in rows(document, mapping["days"]):
        day = date.fromisoformat(field(row, mapping["date"]))
        midnight = datetime.combine(day, time.min, ZoneInfo("Europe/Kyiv"))
        groups = days.setdefault(str(int(midnight.timestamp())), {})
        queue = field(row, mapping["queue"])
        slots = field(row, mapping["slots"])
        if not isinstance(queue, str) or not queue or queue in groups:
            raise ValueError("Ambiguous or incomplete daily schedule")
        groups[queue] = translate_slots(slots, labels, mapping["statuses"])
    if not days:
        raise ValueError("No official daily schedules")
    weekly = {}
    if "weekly" in mapping:
        for group, weekdays in field(document, mapping["weekly"]).items():
            weekly[group] = {
                weekday: translate_slots(slots, labels, mapping["statuses"])
                for weekday, slots in weekdays.items()
            }
    return {"data": weekly, "time_zone": labels}, {"data": days}
