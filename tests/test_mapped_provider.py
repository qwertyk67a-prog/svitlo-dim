import asyncio
import json

import pytest

from conftest import TEST_CONFIG
from power_config import provider_config
from provider_transport import get_homes, get_schedule


def test_read_mapped_official_feeds(monkeypatch):
    mapping = {
        "addresses": [], "city": ["city"], "street": ["street"], "house": ["number"],
        "queues": ["groups"], "labels": ["times"], "days": ["daily"],
        "date": ["date"], "queue": ["q"], "slots": ["data"],
        "statuses": {"power": "yes", "outage": "no"}, "weekly": ["week"],
    }
    config = {**TEST_CONFIG, "adapter": "mapped_json_v1", "mapping": mapping,
              "ajax_url": "https://other.example.invalid/days"}
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(config))
    conf = provider_config()
    address = [
        {"city": "A", "street": "B", "number": 2, "groups": "Q"},
        {"city": "C", "street": "B", "number": 2, "groups": "wrong"},
    ]
    slots = {"1": "power", "2": "outage"}
    row = {"date": "2026-01-02", "q": "Q", "data": slots}
    feed = {"times": {"1": "00-12", "2": "12-24"}, "daily": [row],
            "week": {"Q": {"1": slots}}}

    async def read(url):
        return {conf["site_url"]: address, conf["ajax_url"]: feed}[url]

    monkeypatch.setattr("provider_transport._read_json", read)
    assert asyncio.run(get_homes(conf, "A", "B")).houses["2"].group_ids == ["Q"]
    result = asyncio.run(get_schedule(conf))
    assert next(iter(result.fact.days.values()))["Q"] == {"1": "yes", "2": "no"}
    assert result.preset.groups["Q"].get_day(1).slots["2"] == "no"
    for bad in ([], [row, row], [{**row, "data": {"1": "power"}}],
                [{**row, "data": {**slots, "2": "unknown"}}]):
        feed["daily"] = bad
        with pytest.raises(ValueError):
            asyncio.run(get_schedule(conf))
