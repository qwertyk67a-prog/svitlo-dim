from datetime import datetime
from schedule_board import KYIV, answer_commands, quiet, update_pin, build_board
from types import SimpleNamespace
from bot import parse_verified, compact_daily_message


def test_kyiv_silent_boundaries():
    for hour, minute, expected in [(20,59,False),(21,0,True),(8,59,True),(9,0,False)]:
        assert quiet(datetime(2026,11,23,hour,minute,tzinfo=KYIV)) == expected


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
    assert render(now) == "23.11.2026\nВідключення:\n00:00–12:00"
    source.fact.days[stamp]["q"] = {"1":"yes"}
    assert "00:00–12:00\nОстанні збережені дані" in render(now)
    assert "00:00–12:00" not in render(now.replace(day=24))
