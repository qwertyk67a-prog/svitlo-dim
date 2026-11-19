"""Regression tests for manual Telegram connection."""
import time
import pytest
from scripts import telegram_setup as setup


def test_discovery_ignores_old_private_and_other_bot_commands():
    now = 1900000000
    updates = [
        {"message":{"date":now-20,"chat":{"type":"supergroup","id":-10012},"text":"/chatid"}},
        {"message":{"date":now-1900,"chat":{"type":"supergroup","id":-10013},"text":"/chatid"}},
        {"message":{"date":now-30,"chat":{"type":"private","id":1234},"text":"/chatid"}},
        {"message":{"date":now-12,"chat":{"type":"group","id":-10014},"text":"/chatid@OtherBot"}},
    ]
    assert setup.recent_group_commands(updates,"MyBot",now) == [-10012]


def test_discover_never_sends_and_does_not_print_token(monkeypatch,capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","private-token")
    monkeypatch.setenv("SETUP_MODE","discover")
    calls=[]
    def fake(token,method,**kwargs):
        calls.append(method)
        if method=="getMe":
            return {"is_bot":True,"username":"MyBot"}
        if method=="getUpdates":
            return [{"message":{"date":time.time(),"chat":{"type":"supergroup","id":-100123},"text":"/chatid"}}]
        raise AssertionError("Unexpected Telegram API call")
    monkeypatch.setattr(setup,"call_telegram",fake)
    setup.main()
    output=capsys.readouterr().out
    assert "TELEGRAM_CHAT_ID = -100123" in output
    assert "private-token" not in output
    assert calls==["getMe","getUpdates"]


def test_test_mode_sends_only_to_verified_group(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","private-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID","-100123")
    monkeypatch.setenv("SETUP_MODE","test")
    calls=[]
    def fake(token,method,**kwargs):
        calls.append((method,kwargs))
        if method=="getMe":
            return {"is_bot":True,"username":"MyBot"}
        if method=="getChat":
            return {"id":-100123,"type":"supergroup"}
        if method=="sendMessage":
            return {"message_id":17}
        raise AssertionError("Unexpected call")
    monkeypatch.setattr(setup,"call_telegram",fake)
    setup.main()
    assert [x[0] for x in calls]==["getMe","getChat","sendMessage"]
    assert calls[-1][1]["chat_id"]=="-100123"


def test_invalid_group_never_sends(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","private-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID","123")
    monkeypatch.setenv("SETUP_MODE","test")
    methods=[]
    def fake(_token,method,**_kw):
        methods.append(method)
        return {"is_bot":True,"username":"MyBot"}
    monkeypatch.setattr(setup,"call_telegram",fake)
    with pytest.raises(setup.SetupError,match="ID групи"):
        setup.main()
    assert methods==["getMe"]


def test_api_error_does_not_expose_token(monkeypatch):
    class Response:
        def json(self):
            return {"ok":False,"error_code":401,"description":"bad url"}
    monkeypatch.setattr(setup.requests,"post",lambda *_args,**_kwargs:Response())
    with pytest.raises(setup.SetupError) as exc:
        setup.call_telegram("private-token","getMe")
    assert "private-token" not in str(exc.value)
