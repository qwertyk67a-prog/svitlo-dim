"""Integration tests: one house, one recipient, persistent on-disk state."""
import asyncio
import hashlib
import hmac
import os
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import bot
from conftest import TEST_CONFIG

KYIV=ZoneInfo("Europe/Kyiv")


def fake_schedule(group="GPV3.1", days=2, has_week=False):
    today=datetime.now(KYIV).date()
    raw={}
    for i in range(days):
        day=today+timedelta(days=i)
        stamp=str(int(datetime(day.year,day.month,day.day,tzinfo=KYIV).timestamp()))
        raw[stamp]={group: {"1":"no","2":"yes"}}
    return SimpleNamespace(
        fact=SimpleNamespace(days=raw,update="12.11.2026 17:00"),
        preset=SimpleNamespace(time_zone={"1":"00-24","2":"12-24"},
                               is_active=has_week,groups={}),
    ),[group]


def setup(monkeypatch,path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","token-for-test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID","-100-test")
    monkeypatch.setenv("STATE_PATH",str(path))
    monkeypatch.setenv("ANNOUNCEMENTS_ENABLED","false")
    monkeypatch.setenv("REMINDER_MINUTES","0")
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(TEST_CONFIG, ensure_ascii=False))
    import alerts
    async def default_home(_private_address):
        return address_entry(["GPV3.1"])
    monkeypatch.setattr(alerts, "fetch_address_status", default_home)


def seed_state(path, content):
    from power_config import provider_config, checkpoint_namespace
    identity = checkpoint_namespace(
        provider_config(),
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    path.write_text(json.dumps({"config:namespace": identity, **content}),
                    encoding="utf-8")


def test_successful_notification_persisted_when_next_one_fails(tmp_path,monkeypatch):
    state_file=tmp_path/"state.json"
    setup(monkeypatch,state_file)
    async def load(*_groups):return fake_schedule()
    monkeypatch.setattr(bot,"get_schedule",load)
    calls=[]
    def send(_token,_chat,text,deadline=None):
        calls.append(text)
        if len(calls)==2:
            raise RuntimeError("Network fails after first successful delivery")
        return 1
    monkeypatch.setattr(bot,"telegram_send",send)
    with pytest.raises(RuntimeError,match="Помилки Telegram"):
        asyncio.run(bot.once())
    stored=bot.state_read(state_file)
    day=datetime.now(KYIV).date()
    assert f"{day.isoformat()}:GPV3.1" in stored
    assert f"{(day+timedelta(days=1)).isoformat()}:GPV3.1" not in stored
    assert len(calls)==2


def test_unchanged_data_is_silent_across_restarts(tmp_path,monkeypatch):
    state_file=tmp_path/"state.json"
    setup(monkeypatch,state_file)
    async def load(*_groups):return fake_schedule(days=1)
    monkeypatch.setattr(bot,"get_schedule",load)
    calls=[]
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _token,_chat,text,deadline=None:calls.append(text))
    asyncio.run(bot.once())
    assert len(calls)==1
    asyncio.run(bot.once())
    assert len(calls)==1


def address_entry(groups):
    return SimpleNamespace(
        group_ids=groups, outage_type="", start_date=None,
        end_date=None, sub_type=None,
    )


def setup_address(monkeypatch):
    import alerts
    current = {"queues": ["GPV3.1"]}

    async def lookup(_secret):
        return address_entry(current["queues"])

    monkeypatch.setattr(alerts, "fetch_address_status", lookup)
    return current


def test_added_queue_produces_notice_and_both_schedules(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch, state_file)
    current = setup_address(monkeypatch)
    current["queues"] = ["GPV3.1", "GPV5.1"]
    requested = []

    async def load(group_ids):
        requested.append(tuple(group_ids))
        today = datetime.now(KYIV).date()
        stamp = str(int(datetime(today.year, today.month, today.day, tzinfo=KYIV).timestamp()))
        source = SimpleNamespace(
            preset=SimpleNamespace(time_zone={"1":"00-24","2":"12-24"},
                                   is_active=False, groups={}),
            fact=SimpleNamespace(
                days={stamp: {
                    "GPV3.1": {"1":"no", "2":"yes"},
                    "GPV5.1": {"1":"yes", "2":"no"},
                }},
                update="12.11.2026 21:00"
            ),
        )
        return source, list(group_ids)

    monkeypatch.setattr(bot, "get_schedule", load)
    calls=[]
    monkeypatch.setattr(
        bot, "telegram_send",
        lambda _t, _c, text, deadline=None: calls.append(text),
    )

    asyncio.run(bot.once())
    assert requested == [("GPV3.1", "GPV5.1")]
    assert len(calls) == 2
    assert sum("ПрикладЕнерго оновив черги" in m or "Уточнено черги" in m for m in calls) == 0
    assert any("Черга 5.1" in m and "Графік відключень" in m for m in calls)
    assert any("Черга 3.1" in m and "Графік відключень" in m for m in calls)
    stored = bot.state_read(state_file)
    assert stored["house:groups"] == ["GPV3.1","GPV5.1"]
    assert not stored.get("house:queue_hold")

    asyncio.run(bot.once())
    assert len(calls) == 2


def test_removed_queue_keeps_remaining_schedule_and_one_notice(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch, state_file)
    current = setup_address(monkeypatch)
    current["queues"] = ["GPV5.1"]
    seed_state(state_file, {
        "house:groups": ["GPV3.1", "GPV5.1"],
        "2026-01-01:GPV3.1": {"status":"outages","digest":"old"},
    })

    async def load(group_ids):
        return fake_schedule(group="GPV5.1", days=1)
    monkeypatch.setattr(bot, "get_schedule", load)
    calls=[]
    monkeypatch.setattr(
        bot, "telegram_send",
        lambda _t,_c,text,deadline=None: calls.append(text)
    )
    asyncio.run(bot.once())
    assert len(calls) == 2
    assert "Вилучено: 3.1" in calls[0]
    assert any("Черга 5.1" in m for m in calls)
    asyncio.run(bot.once())
    assert len(calls) == 2


def test_missing_address_data_never_sends_false_queue_change(tmp_path, monkeypatch):
    state_file=tmp_path/"state.json"
    setup(monkeypatch, state_file)
    current=setup_address(monkeypatch)
    current["queues"]=[]
    calls=[]
    monkeypatch.setattr(
        bot,"telegram_send",
        lambda _t,_c,text,deadline=None: calls.append(text)
    )
    async def should_not_run(*args):
        raise AssertionError("No queue schedule should be sent without confirmation")
    monkeypatch.setattr(bot,"get_schedule",should_not_run)
    with pytest.raises(RuntimeError, match="address_lookup"):
        asyncio.run(bot.once())
    assert calls == []
    assert "house:groups" not in json.loads(state_file.read_text())


def test_failed_queue_notice_retries_but_schedules_continue(tmp_path,monkeypatch):
    state_file=tmp_path/"state.json"
    setup(monkeypatch,state_file)
    current=setup_address(monkeypatch)
    current["queues"]=["GPV5.1"]
    seed_state(state_file, {"house:groups":["GPV3.1"]})
    calls=[]
    async def load(groups):
        return fake_schedule(group="GPV5.1",days=1)
    monkeypatch.setattr(bot,"get_schedule",load)

    def send(_t,_c,text,deadline=None):
        calls.append(text)
        if "ПрикладЕнерго оновив черги" in text:
            raise RuntimeError("Telegram unavailable for queue-change notice")
    monkeypatch.setattr(bot,"telegram_send",send)
    with pytest.raises(RuntimeError,match="Помилки Telegram"):
        asyncio.run(bot.once())
    assert any("Графік відключень" in x for x in calls)
    assert bot.state_read(state_file)["house:groups"] == ["GPV3.1"]
    # The schedule was delivered once; the one-time queue notice is retried.
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _t,_c,text,deadline=None: calls.append(text))
    asyncio.run(bot.once())
    assert sum("ПрикладЕнерго оновив черги" in x for x in calls) == 2
    assert sum("Графік відключень" in x for x in calls) == 1


def test_migrating_old_queue_hold_does_not_pause_anymore(tmp_path,monkeypatch):
    state_file=tmp_path/"state.json"
    setup(monkeypatch,state_file)
    current=setup_address(monkeypatch)
    current["queues"]=["GPV5.1","GPV3.1"]
    seed_state(state_file, {
        "house:queue_hold": {
            "groups":["GPV5.1"], "notified":True,"code":"old-code"
        }
    })
    async def load(groups):
        return fake_schedule(group="GPV3.1",days=1)
    monkeypatch.setattr(bot,"get_schedule",load)
    calls=[]
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _t,_c,text,deadline=None:calls.append(text))
    asyncio.run(bot.once())
    assert "house:queue_hold" not in json.loads(state_file.read_text())
    assert not any("5.1" in m and "Додано" in m for m in calls)
    assert any("Графік відключень" in m for m in calls)



class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 19, 20, 30, tzinfo=tz or KYIV)


def _late_schedule(slots):
    day=_FixedDatetime.now(KYIV).date()
    stamp=str(int(datetime(day.year,day.month,day.day,tzinfo=KYIV).timestamp()))
    return SimpleNamespace(
        preset=SimpleNamespace(
            is_active=False,
            time_zone={"1":"00-12","2":"12-20","3":"20-24"},
            groups={},
        ),
        fact=SimpleNamespace(days={stamp:{"GPV3.1":slots}},
                             update="19.11.2026 20:28"),
    ),["GPV3.1"]


def _late_test_setup(tmp_path,monkeypatch,initial):
    path=tmp_path/"late_state.json"
    setup(monkeypatch,path)
    monkeypatch.setattr(bot,"datetime",_FixedDatetime)
    current={"source":initial}
    async def load(*_groups):
        return _late_schedule(current["source"])
    monkeypatch.setattr(bot,"get_schedule",load)
    sent=[]
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _t,_c,body,deadline=None:sent.append(body))
    return path,current,sent


def test_outage_published_after_it_ended_is_silent(tmp_path,monkeypatch):
    path,current,sent=_late_test_setup(
        tmp_path,monkeypatch,
        {"1":"no","2":"no","3":"yes"},
    )
    asyncio.run(bot.once())
    asyncio.run(bot.once())
    assert sent == []
    state=bot.state_read(path)
    key="2026-11-19:GPV3.1"
    assert state[key]["status"]=="historical_only"
    # The next update adds an ongoing outage. Only that new information matters.
    current["source"]={"1":"no","2":"no","3":"no"}
    asyncio.run(bot.once())
    assert len(sent)==1
    assert "Графік відключень" in sent[0]
    assert "час відключення вже настав" in sent[0]


def test_outage_start_past_but_end_future_triggers_notice(tmp_path,monkeypatch):
    path,current,sent=_late_test_setup(
        tmp_path,monkeypatch,
        {"1":"yes","2":"yes","3":"no"},
    )
    asyncio.run(bot.once())
    assert len(sent)==1
    assert "20:00–24:00" in sent[0]
    assert "час відключення вже настав" in sent[0]


def test_past_only_change_does_not_repeat_sent_ongoing_schedule(tmp_path,monkeypatch):
    path,current,sent=_late_test_setup(
        tmp_path,monkeypatch,
        {"1":"no","2":"yes","3":"no"},
    )
    asyncio.run(bot.once())
    assert len(sent)==1
    current["source"]={"1":"yes","2":"yes","3":"no"}
    asyncio.run(bot.once())
    assert len(sent)==1
    asyncio.run(bot.once())
    assert len(sent)==1


def test_today_schedule_removes_active_outage_and_notifies(tmp_path,monkeypatch):
    path,current,sent=_late_test_setup(
        tmp_path,monkeypatch,
        {"1":"yes","2":"yes","3":"no"},
    )
    asyncio.run(bot.once())
    assert len(sent)==1
    current["source"]={"1":"no","2":"yes","3":"yes"}
    asyncio.run(bot.once())
    assert len(sent)==2
    assert "На решту дня" in sent[1]
    assert "скасовано" in sent[1]
    asyncio.run(bot.once())
    assert len(sent)==2


def test_only_old_finished_outage_added_to_new_graph_no_notice(
        tmp_path,monkeypatch):
    path,current,sent=_late_test_setup(
        tmp_path,monkeypatch,
        {"1":"yes","2":"yes","3":"yes"},
    )
    asyncio.run(bot.once())
    assert sent == []
    current["source"]={"1":"no","2":"yes","3":"yes"}
    asyncio.run(bot.once())
    assert sent == []


def test_address_api_failure_uses_last_verified_queues(tmp_path, monkeypatch):
    """The address endpoint is not a single point of failure."""
    state_file = tmp_path / "state.json"
    setup(monkeypatch, state_file)
    setup_address(monkeypatch)
    import alerts
    async def unavailable(_secret):
        raise RuntimeError("provider temporary error")
    monkeypatch.setattr(alerts, "fetch_address_status", unavailable)
    seed_state(state_file, {"house:groups": ["GPV3.1"]})
    checked = []
    async def schedules(groups):
        checked.append(groups)
        return fake_schedule(days=1)
    monkeypatch.setattr(bot, "get_schedule", schedules)
    messages = []
    monkeypatch.setattr(bot, "telegram_send",
                        lambda _t, _c, msg, deadline=None: messages.append(msg))
    asyncio.run(bot.once())
    assert checked == [("GPV3.1",)]
    assert len(messages) == 1


def test_address_failure_without_cache_does_not_invent_group(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch, state_file)
    setup_address(monkeypatch)
    import alerts
    async def unavailable(_secret):
        raise RuntimeError("provider temporary error")
    monkeypatch.setattr(alerts, "fetch_address_status", unavailable)
    called = []
    async def schedules(*args):
        called.append(args)
        return fake_schedule()
    monkeypatch.setattr(bot, "get_schedule", schedules)
    with pytest.raises(RuntimeError, match="address_lookup"):
        asyncio.run(bot.once())
    assert called == []


def test_no_address_is_disabled_unless_explicitly_approved(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch, state_file)
    monkeypatch.delenv("POWER_CONFIG_JSON")
    called = []
    async def schedules(*args):
        called.append(args)
        return fake_schedule()
    monkeypatch.setattr(bot, "get_schedule", schedules)
    with pytest.raises(RuntimeError, match="POWER_CONFIG_JSON"):
        asyncio.run(bot.once())
    assert called == []


def test_dry_run_never_sends_telegram(tmp_path, monkeypatch):
    state_file = tmp_path / "dry-run-only.json"
    setup(monkeypatch, state_file)
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    async def schedules(*_groups):
        return fake_schedule(days=1)
    monkeypatch.setattr(bot, "get_schedule", schedules)
    monkeypatch.setattr(bot, "telegram_send",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("DRY_RUN must never send")))
    asyncio.run(bot.once())
    assert not state_file.exists()


def test_dry_run_does_not_change_existing_state(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch, state_file)
    monkeypatch.setenv("DRY_RUN", "true")
    state_file.write_text('{"unchanged": true}', encoding="utf-8")
    async def schedules(*_groups):
        return fake_schedule(days=1)
    monkeypatch.setattr(bot, "get_schedule", schedules)
    monkeypatch.setattr(bot, "telegram_send",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("No Telegram calls in dry-run")))
    asyncio.run(bot.once())
    assert json.loads(state_file.read_text()) == {"unchanged": True}


def test_active_emergency_marks_daily_schedule_as_conditional(tmp_path,monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch,state_file)
    seed_state(state_file, {
        "official:status:emergency:region":{"status":"active"},
    })
    async def load(*_groups):
        return fake_schedule(days=1)
    monkeypatch.setattr(bot,"get_schedule",load)
    messages=[]
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _t,_c,txt,deadline=None: messages.append(txt))
    asyncio.run(bot.once())
    assert len(messages)==1
    assert "погодинний графік може не діяти" in messages[0]


def test_official_feed_error_marks_action_failed_after_schedule_delivery(
        tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch,state_file)
    monkeypatch.setenv("ANNOUNCEMENTS_ENABLED","true")
    setup_address(monkeypatch)
    import alerts
    monkeypatch.setattr(alerts,"fetch_official_page",
                        lambda: (_ for _ in ()).throw(RuntimeError("WAF")))
    async def load(group_ids):
        return fake_schedule(days=1)
    monkeypatch.setattr(bot,"get_schedule",load)
    messages=[]
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _t,_c,txt,deadline=None: messages.append(txt))
    with pytest.raises(RuntimeError,match="official_feed"):
        asyncio.run(bot.once())
    assert messages  # Valid daily GPV was not blocked by official feed outage.


def test_official_feed_runs_even_if_address_api_unavailable(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch,state_file)
    monkeypatch.setenv("ANNOUNCEMENTS_ENABLED","true")
    import alerts
    async def fail(_secret):
        raise RuntimeError("provider API offline")
    monkeypatch.setattr(alerts, "fetch_address_status", fail)
    now = datetime.now(KYIV)
    def news():
        return ('<div class="tgme_widget_message" data-post="sample_energy/999">'
                '<div class="tgme_widget_message_text">'
                'Прикладщина: запроваджено екстрені відключення'
                '</div><time datetime="' + now.isoformat() + '">x</time></div>')
    monkeypatch.setattr(alerts,"fetch_official_page",news)
    async def should_not_fetch(*args):
        raise AssertionError("No unverified GPV should be fetched")
    monkeypatch.setattr(bot, "get_schedule", should_not_fetch)
    messages=[]
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _t,_c,txt,deadline=None: messages.append(txt))
    with pytest.raises(RuntimeError, match="address_lookup"):
        asyncio.run(bot.once())
    assert len(messages)==1
    assert "екстрені" in messages[0].casefold()


def test_multi_queue_schedule_disclaims_apartment_uncertainty(tmp_path,monkeypatch):
    state_file = tmp_path / "state.json"
    setup(monkeypatch,state_file)
    setup_address(monkeypatch)
    import alerts
    from types import SimpleNamespace
    async def two_queues(_secret):
        return SimpleNamespace(group_ids=["GPV5.1","GPV3.1"],
                               outage_type="",start_date=None,
                               end_date=None,sub_type=None)
    monkeypatch.setattr(alerts,"fetch_address_status",two_queues)
    async def load(groups):
        source, _ = fake_schedule(days=1)
        dt = datetime.now(KYIV).date()
        stamp = str(int(datetime(dt.year,dt.month,dt.day,tzinfo=KYIV).timestamp()))
        source.fact.days[stamp]["GPV5.1"] = dict(source.fact.days[stamp]["GPV3.1"])
        return source,list(groups)
    monkeypatch.setattr(bot,"get_schedule",load)
    sent=[]
    monkeypatch.setattr(bot,"telegram_send",
                        lambda _t,_c,text,deadline=None:sent.append(text))
    asyncio.run(bot.once())
    schedules=[x for x in sent if "Графік відключень" in x]
    assert len(schedules)==2
    assert all("Розподіл квартир між чергами не підтверджено" in x for x in schedules)
