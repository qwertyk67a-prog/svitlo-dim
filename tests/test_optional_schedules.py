import asyncio
from datetime import datetime, timedelta

from bot import KYIV
from optional_schedules import mirror_section, subscription_command, notify_subscriptions
from scripts.replay_schedule import replay


def test_mirror_freshness_region_and_empty_records():
    now = datetime(2026, 11, 26, 9, tzinfo=KYIV)
    stamp = str(int(now.timestamp()))
    rows = {"1": "yes", "2": "no"}
    doc = {"regionId": "example", "lastUpdated": now.isoformat(),
           "lastUpdateStatus": {"ok": True, "at": now.isoformat()},
           "preset": {"time_zone": {"1":"00-12","2":"12-24"}, "data": {}},
           "fact": {"data": {stamp: {"G": rows}}}}
    assert replay(doc, "G", now.date()).endswith("12:00–24:00")
    assert "Неофіційне джерело" in mirror_section(doc, "example", ["G"], now)
    assert not mirror_section(doc, "other", ["G"], now)
    assert not mirror_section(doc, "example", ["G"], now + timedelta(minutes=31))
    rows["2"] = "yes"
    assert not mirror_section(doc, "example", ["G"], now)


def test_subscriptions_check_membership_and_revocation():
    homes = {"one": ("Перший", "-1", {}), "two": ("Другий", "-2", {})}
    state, messages = {}, []
    def call(method, **kw):
        if method == "sendMessage":
            messages.append(kw["reply_markup"]["inline_keyboard"])
            return {}
        return {"status": "member" if kw["chat_id"] == "-1" else "left"}
    def speak(user, text):
        messages.append(text)
    msg = {"chat": {"type":"private","id":1}, "text":"/sub_one"}
    subscription_command(msg, homes, state, call, speak, lambda:None)
    assert state["subscriptions"]["1"] == {"one":""}
    assert messages[-1] == [[{"text":"☑ Перший","callback_data":"/unsub_one"}]]
    asyncio.run(notify_subscriptions(homes, state, lambda *a, **k: {"status":"left"},
                                     speak, lambda:None, {}, [], None, datetime.now(KYIV), False))
    assert state["subscriptions"] == {}


def test_valid_official_reserve_prevents_mirror(monkeypatch):
    from optional_schedules import reserve_view
    import provider_transport
    now = datetime(2026, 11, 26, 9, tzinfo=KYIV)
    calls = []
    async def fetch(url):
        calls.append(url)
        return {"1.1": {"today": {"status":"ScheduleApplies", "date":now.isoformat(),
                                  "slots":[{"start":0,"end":1440,"type":"NotPlanned"}]}}}
    monkeypatch.setattr(provider_transport, "_read_json", fetch)
    config = {"official_reserve_url":"https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/0/dsos/0/planned-outages",
              "mirror_region":"example"}
    assert "YASNO" in asyncio.run(reserve_view(config, ["GPV1.1"], now, True))
    assert len(calls) == 1


def test_registry_requires_both_host_and_operator_and_ignores_translated_mirrors():
    from source_registry import match_sources
    doc = """Operator("id","Test","region","https://test.invalid",sources=(
        Yasno(region=1,dso=2), OutageDataUa("example", map_groups)))"""
    assert match_sources(doc, "https://other.invalid", "Test") == {}
    assert match_sources(doc, "https://test.invalid", "Other") == {}
    values = match_sources(doc, "https://test.invalid/page", "Test")
    assert "mirror_region" not in values
    assert values["official_reserve_url"].endswith("/1/dsos/2/planned-outages")


def test_official_waiting_status_never_falls_through_to_mirror(monkeypatch):
    from optional_schedules import reserve_view
    import provider_transport
    now = datetime(2026, 11, 26, 9, tzinfo=KYIV)
    calls = []
    async def fetch(url):
        calls.append(url)
        return {"1.1":{"today":{"status":"WaitingForSchedule","date":now.isoformat()}}}
    monkeypatch.setattr(provider_transport, "_read_json", fetch)
    config = {"official_reserve_url":"https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/0/dsos/0/planned-outages",
              "mirror_region":"example"}
    assert not asyncio.run(reserve_view(config, ["GPV1.1"], now, True))
    assert len(calls) == 1


def test_private_button_retry_and_wrong_sender():
    homes = {"one": ("Дім", "-1", {})}
    state, edits = {}, []
    def call(method, **kwargs):
        if method == "getChatMember":
            return {"status":"member"}
        edits.append(method)
    msg = {"message_id":10, "chat":{"id":21, "type":"private"}}
    callback = {"from":{"id":72}, "data":"/sub_one", "id":"example"}
    subscription_command(msg, homes, state, call, None, lambda:None, callback)
    assert state == {}
    callback["from"]["id"] = 21
    for _ in range(2):
        subscription_command(msg, homes, state, call, None, lambda:None, callback)
    assert state["subscriptions"] == {"21":{"one":""}}
    assert edits == ["editMessageText","answerCallbackQuery"] * 2
