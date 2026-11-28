from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import schedule_board
from schedule_board import KYIV, answer_commands, quiet, update_pin, build_board
from types import SimpleNamespace
from bot import parse_verified, compact_daily_message


def test_kyiv_silent_boundaries(monkeypatch):
    from astral import Observer

    monkeypatch.setattr(schedule_board, "city_observer", lambda: Observer(48, 33))
    schedule_board.city_sunset.cache_clear()
    winter = datetime(2026, 11, 27, 10, tzinfo=KYIV)
    summer = datetime(2026, 6, 21, 20, tzinfo=KYIV)
    end = schedule_board.city_sunset(winter.date())
    tick = timedelta(microseconds=1)
    assert quiet(winter - tick) and not quiet(winter)
    assert not quiet(end - tick) and quiet(end.astimezone(timezone.utc))
    assert not quiet(summer - tick) and quiet(summer)
    schedule_board.city_sunset.cache_clear()


def test_pin_uses_quiet_flag():
    for silent in (True, False):
        with patch("schedule_board.quiet", return_value=silent):
            calls = []
            def call(method, **args):
                calls.append((method, args))
                return {}
            update_pin({}, "text", "chat", call, lambda *_: 7, lambda: None)
            assert calls[-1] == ("pinChatMessage", dict(chat_id="chat", message_id=7,
                                                        disable_notification=silent))


def test_board_only_edits_its_own_card_after_migration():
    state = {"board:owner": "12"}
    calls = []
    top = {"message_id": 10, "from":{"id":12}, "text":"23.11.2026\nВідключення:\n10:00–12:00"}
    def call(method, **args):
        if method == "getChat":
            return {"pinned_message":top}
        calls.append((method,args["message_id"]))
        if method == "pinChatMessage":
            top["message_id"] = args["message_id"]
        return True
    def send(chat,text):
        calls.append(("send", 11))
        return 11
    for text in ("first","first","updated"):
        update_pin(state,text,"123",call,send,lambda:None)
    assert calls == [("send",11),("pinChatMessage",11),("unpinChatMessage",10),("editMessageText",11)]


def test_now_enforces_membership_and_saves_offset():
    state = {}
    sent = []
    def call(method, **args):
        if method == "getChatMember":
            return {"status":"member" if args["user_id"] == 2 else "left"}
        if method == "getMe":
            return {"username":"Example_bot"}
        rows = [(-1,"group","/now"),(2,"private","/now@Example_bot"),(3,"private","/now"),
                (-4,"group","/now"),(-1,"group","/now@Other_bot")]
        return [{"update_id":i,"message":{"text":cmd,"chat":{"id":n,"type":k}}}
                for i,(n,k,cmd) in enumerate(rows,1) if i >= args["offset"]]
    for _ in range(2):
        answer_commands(state,"saved","-1",call,lambda n,t:sent.append(n),lambda:None)
    assert sent == [-1,2]
    assert state["board:offset"] == 6


def test_invalid_table_keeps_cache_and_rollover_removes_yesterday():
    now = datetime(2026,11,23,16,0,tzinfo=KYIV)
    stamp = str(int(now.timestamp()))
    source = SimpleNamespace(fact=SimpleNamespace(days={stamp:{"q":{"1":"no","2":"yes"}}}),
                             preset=SimpleNamespace(time_zone={"1":"00-12","2":"12-24"}))
    cache = {}
    def render(day):
        return build_board(cache,source,["q"],day,False,None,True,parse_verified,compact_daily_message)
    assert render(now) == "23.11.2026\n00:00–12:00"
    source.fact.days[stamp]["q"] = {"1":"yes"}
    assert "00:00–12:00\nОстанні збережені дані" in render(now)
    assert "00:00–12:00" not in render(now.replace(day=24))


def test_combined_week_respects_facts_stale_forecast_and_sunday_boundary():
    now = datetime(2026, 11, 25, 12, tzinfo=KYIV)
    slots = {"1": "no", "2": "yes"}
    forecast = SimpleNamespace(get_day=lambda weekday: SimpleNamespace(slots=slots))
    preset = SimpleNamespace(is_active=True, time_zone={"1":"00-12","2":"12-24"}, groups={"q": forecast})
    source = SimpleNamespace(preset=preset, fact=SimpleNamespace(days={
        str(int(now.timestamp())): {"q": {"1":"yes","2":"no"}}
    }))
    cache = {}
    def render(source, week=True):
        return build_board(cache, source, ["q"], now, False, None, True,
                           parse_verified, compact_daily_message, include_week=week)
    text = render(source)
    assert text.count("25.11.2026") == 1
    assert "25.11.2026\n12:00–24:00" in text
    assert "\n\n26.11.2026\n00:00–12:00\n\n27.11.2026" in text
    assert "Тижневий" not in text
    assert "30.11.2026" not in text
    assert "01.12.2026" not in text
    assert "останні збережені дані" in render(None)
    assert "Тижневий" not in render(source, False)


def test_combined_board_omits_empty_weekend_and_address_footer():
    now = datetime(2026, 11, 27, 20, tzinfo=KYIV)
    labels = {"1": "00-10", "2": "10:00-16:30", "3": "16:30-19:00",
              "4": "19:00-22:30", "5": "22:30-24:00"}
    clear = dict.fromkeys(labels, "yes")
    friday = dict(clear, **{"2": "no", "4": "no"})
    forecast = SimpleNamespace(get_day=lambda weekday: SimpleNamespace(slots=clear))
    preset = SimpleNamespace(is_active=True, time_zone=labels, groups={"q": forecast})
    source = SimpleNamespace(preset=preset, fact=SimpleNamespace(days={
        str(int(now.timestamp())): {"q": friday}}))
    address = SimpleNamespace(start_date="27.11.2026 19:30", end_date="27.11.2026 23:27")
    cache = {}
    def render():
        return build_board(cache, source, ["q"], now, False, address, True,
                           parse_verified, compact_daily_message, include_week=True)
    expected = "27.11.2026\n10:00–16:30\n19:00–22:30"
    assert render() == expected
    address.end_date = "27.11.2026 23:45"
    assert render() == expected
