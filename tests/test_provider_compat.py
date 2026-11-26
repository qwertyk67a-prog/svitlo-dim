"""Regression tests for the generic provider settings and compatible API adapter."""
import json
from types import SimpleNamespace

import pytest

from conftest import TEST_CONFIG
from power_config import ConfigurationError, provider_config, address_json
from provider_transport import form_data, parse_homes, parse_preset, parse_fact
from alerts import get_house_entry


def test_real_properties_come_from_only_private_configuration(monkeypatch):
    candidate=json.loads(json.dumps(TEST_CONFIG))
    candidate["address"]["house"]="43/Б"
    candidate["operator"]="Нова мережа"
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(candidate,ensure_ascii=False))
    conf=provider_config()
    assert conf["address"]["house"]=="43/Б"
    assert conf["operator"]=="Нова мережа"
    assert json.loads(address_json())["house"]=="43/Б"


def test_unsupported_provider_adapter_rejected(monkeypatch):
    config={**TEST_CONFIG,"adapter":"unknown"}
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(config,ensure_ascii=False))
    with pytest.raises(ConfigurationError,match="Unsupported provider adapter"):
        provider_config()


@pytest.mark.parametrize("bad", [
    "http://grid.example.invalid/ua/ajax",
    "https://user:pass@grid.example.invalid/ua/ajax",
    "https://another.example.invalid/ua/ajax",
])
def test_unsafe_or_mismatched_api_urls_rejected(monkeypatch,bad):
    config={**TEST_CONFIG,"ajax_url":bad}
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(config,ensure_ascii=False))
    with pytest.raises(ConfigurationError):
        provider_config()


def test_form_encoding_matches_compatible_api_protocol():
    form=form_data("getHomeNum",[("city","Тестоград"),("street","Вулиця")],
                   update_fact="01.01.2000 00:00")
    assert form["method"]=="getHomeNum"
    assert form["data[0][name]"]=="city"
    assert form["data[0][value]"]=="Тестоград"
    assert form["data[2][value]"]=="01.01.2000 00:00"


def test_house_parser_keeps_all_groups_and_emergency_reason():
    response=parse_homes({"data":{
        "42/А":[
            {"sub_type_reason":["GPV5.1"]},
            {"sub_type_reason":["GPV3.1"],"start_date":"20.11.2026 10:00",
             "end_date":"20.11.2026 12:00","type":"2","sub_type":"аварія"},
        ],
        "11":{"sub_type_reason":["GPV1.1"]},
    }})
    a=get_house_entry(response,"42А")
    assert a.group_ids==["GPV5.1","GPV3.1"]
    assert a.start_date=="20.11.2026 10:00"
    assert a.outage_type=="2"
    with pytest.raises(RuntimeError):
        get_house_entry(response,"1/1")


def test_weekly_and_daily_parsers_preserve_time_and_slots():
    preset=parse_preset({
        "data":{"GPV2.1":{"1":{"1":"no","2":"yes"}}},
        "time_zone":{"1":["00:00-00:30"],"2":"00:30-01:00"},
    })
    assert preset.is_active
    assert preset.time_zone["1"]=="00:00-00:30"
    assert preset.groups["GPV2.1"].get_day(1).slots["1"]=="no"
    fact=parse_fact({"data":{"1900000000":{"GPV2.1":{"1":"no","2":"yes"}}},
                     "update":"20.11.2026 08:00"})
    assert fact.days["1900000000"]["GPV2.1"]["1"]=="no"
    assert fact.update=="20.11.2026 08:00"


def test_missing_or_broken_schedules_are_never_interpreted_as_cancellation():
    with pytest.raises(RuntimeError):
        parse_preset({"data":{},"time_zone":[]})
    with pytest.raises(RuntimeError):
        parse_fact({"data":[]})


def test_no_private_provider_data_in_live_source_tree():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    production=[
        "bot.py", "alerts.py", "event_scope.py",
        "house_reliability.py","power_config.py","provider_transport.py",
        ".github/workflows/check.yml",
        ".github/workflows/audit-dry-run.yml",
        ".github/workflows/verify-address.yml",
        "requirements.txt",".env.example",
    ]
    for path in production:
        text=(root/path).read_text(encoding="utf-8").casefold()
        assert TEST_CONFIG["operator"].casefold() not in text
        assert TEST_CONFIG["address"]["street"].casefold() not in text
        assert "configured-company.example" not in text
        assert "t.me/s/specificoperator" not in text
        assert "branded_client" not in text
        assert "branded-blackout-client" not in text



def test_public_checkpoint_hides_actual_queue_and_preserves_dedup(tmp_path):
    from bot import state_write, state_read
    path=tmp_path/"bot_state.json"
    state={
        "config:namespace": "fixed-synthetic-hash",
        "house:groups": ["GPV3.1"],
        "week:2026-11-16:GPV3.1": {"digest":"abcdef","status":"outages"},
        "2026-11-20:GPV3.1": {"digest":"1234","status":"outages"},
        "official:last_id": 99,
    }
    state_write(path,state)
    on_disk=path.read_text(encoding="utf-8")
    assert "GPV3.1" not in on_disk
    assert "3.1" not in on_disk
    assert "GPV" not in on_disk
    assert "fixed-synthetic-hash" in on_disk
    assert state_read(path)==state


def test_new_private_state_key_produces_different_anonymous_ids(tmp_path,monkeypatch):
    from bot import state_write, state_read
    path=tmp_path/"state.json"
    state={"house:groups":["GPV3.1"]}
    state_write(path,state)
    before=path.read_text(encoding="utf-8")
    changed=json.loads(json.dumps(TEST_CONFIG))
    changed["state_key"]="fedcba9876543210" * 4
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(changed,ensure_ascii=False))
    # The old public pseudonym cannot be mistaken for a new queue.
    assert state_read(path)!=state
    state_write(path,state)
    after=path.read_text(encoding="utf-8")
    assert before!=after
    assert state_read(path)==state


def test_no_real_names_or_secret_fields_in_public_checkpoint(tmp_path):
    from bot import state_write
    path=tmp_path/"state.json"
    state_write(path,{
        "house:groups":["GPV3.1"],
        "official:event:10":{"status":"active","source":"a2a2a2"},
    })
    visible=path.read_text(encoding="utf-8")
    assert TEST_CONFIG["address"]["city"] not in visible
    assert TEST_CONFIG["address"]["street"] not in visible
    assert TEST_CONFIG["operator"] not in visible
    assert TEST_CONFIG["site_url"] not in visible



def test_provider_specific_queue_catalog_and_overlapping_identifiers(tmp_path,monkeypatch):
    from bot import state_write, state_read
    from house_reliability import verified_house_queues, queue_label
    config=json.loads(json.dumps(TEST_CONFIG))
    config["queue_catalog"]=["GROUP1","GROUP10"]
    config["queue_display_prefix"]="GROUP"
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(config,ensure_ascii=False))
    assert verified_house_queues(["GROUP10","GROUP1"])==("GROUP1","GROUP10")
    assert queue_label("GROUP10")=="10"
    with pytest.raises(ValueError):
        verified_house_queues(["NONEXISTENT"])
    path=tmp_path/"state.json"
    state={"house:groups":["GROUP10","GROUP1"],
           "week:2026-11-16:GROUP10":{"digest":"abc","status":"outages"}}
    state_write(path,state)
    raw=path.read_text(encoding="utf-8")
    assert "GROUP1" not in raw and "GROUP10" not in raw
    assert state_read(path)==state



def test_csrf_cookie_bootstrap_and_masked_header(monkeypatch):
    import asyncio
    import base64
    from urllib.parse import quote
    import provider_transport

    raw_token = "abcdefghijklmnopqrstuvwxyz012345"
    cookie = quote('a:1:{i:1;s:32:"' + raw_token + '";}')
    responses = []

    class Response:
        def __init__(self,status,cookies=None,payload=None):
            self.status_code=status
            self.cookies=cookies or {}
            self._payload=payload or {"result":True}
        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self,**kwargs):
            self.cookies=dict(kwargs.get("cookies",{}))
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,data,headers=None):
            responses.append((data,headers))
            if headers is None or "X-CSRF-Token" not in headers:
                self.cookies["_csrf-example"]=cookie
                return Response(400,{"_csrf-example":cookie})
            payload=base64.urlsafe_b64decode(headers["X-CSRF-Token"])
            size=len(payload)//2
            recovered=bytes(a^b for a,b in zip(
                payload[:size],payload[size:],strict=True
            )).decode()
            assert recovered==raw_token
            return Response(200,payload={"preset":{},"fact":{}})

    monkeypatch.setattr(provider_transport,"AsyncSession",FakeSession)
    config=provider_config()
    result=asyncio.run(provider_transport._request(config,{"method":"checkDisconUpdate"}))
    assert "preset" in result
    assert len(responses)==2
    assert responses[0][0]["method"]=="getHomeNum"
    assert responses[1][0]["method"]=="checkDisconUpdate"


def test_csrf_expired_header_refreshes_once(monkeypatch):
    import asyncio
    import provider_transport
    from urllib.parse import quote

    calls=[]
    token=quote('i:1;s:32:"abcdefghijklmnopqrstuvwxyz012345"')
    class Response:
        def __init__(self,status,cookies=None):
            self.status_code=status
            self.cookies=cookies or {}
        def json(self):return {"result":True}

    class FakeSession:
        def __init__(self,**kwargs):
            self.cookies={}
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,data,headers=None):
            calls.append((data,headers))
            if headers is None or "X-CSRF-Token" not in headers:
                self.cookies["_csrf-site"]=token
                return Response(400,{"_csrf-site":token})
            if sum(x[1] is not None for x in calls)==1:
                return Response(400)
            return Response(200)

    monkeypatch.setattr(provider_transport,"AsyncSession",FakeSession)
    result=asyncio.run(provider_transport._request(
        provider_config(),{"method":"checkDisconUpdate"}
    ))
    assert result["result"] is True
    assert len(calls)==4


def test_source_rate_limit_never_starts_browser_fallback(monkeypatch):
    import asyncio
    import provider_transport
    invoked=[]
    async def limited(*args,**kwargs):
        raise provider_transport.ProviderRateLimit("429")
    async def browser(*args,**kwargs):
        invoked.append("browser")
        raise AssertionError("Do not retry rate-limited provider")
    monkeypatch.setattr(provider_transport,"_request",limited)
    monkeypatch.setattr(provider_transport,"browser_cookies",browser)
    with pytest.raises(provider_transport.ProviderRateLimit):
        asyncio.run(provider_transport.ajax_post(
            provider_config(),{"method":"checkDisconUpdate"}
        ))
    assert invoked==[]



def test_second_city_operator_channel_and_queue_are_private(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from alerts import regional_event, official_posts
    from event_scope import house_scope_from_secret, coverage_for_house
    cfg=json.loads(json.dumps(TEST_CONFIG))
    cfg.update({
        "operator":"Нова мережа",
        "city_label":"Світанкове",
        "city_forms":["світанкове","світанковому"],
        "region_label":"Друга область",
        "region_forms":["друга область","другій області"],
        "other_places":["іншоград"],
        "other_regions":["сусідня область"],
        "headline_prefixes":["нова мережа"],
        "telegram_feed":"https://t.me/s/another_grid",
        "queue_catalog":["Q-A","Q-B"],
        "queue_display_prefix":"",
        "address":{"city":"м. Світанкове","street":"вул. Тестова",
                   "house":"27/Б","district":"","quarter":""},
    })
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(cfg,ensure_ascii=False))
    scope=house_scope_from_secret(address_json())
    covered=coverage_for_house(
        "Світанкове: запроваджено екстрені відключення",scope
    )
    assert covered and covered["level"]=="city"
    assert coverage_for_house(
        "Зелений Бір: запроваджено екстрені відключення",scope
    ) is None
    stamp=datetime.now(ZoneInfo("Europe/Kyiv"))
    page=(
        '<div class="tgme_widget_message" data-post="another_grid/14">'
        '<div class="tgme_widget_message_text">'
        'Світанкове: запроваджено екстрені відключення</div>'
        f'<time datetime="{stamp.isoformat()}"></time></div>'
    )
    posts=official_posts(page)
    assert len(posts)==1
    event=regional_event(posts[0])
    assert event and event["operator"]=="Нова мережа"
    assert event["area"]=="Світанкове"
    assert posts[0]["url"].endswith("another_grid/14")
    from house_reliability import verified_house_queues
    assert verified_house_queues(["Q-B"])==("Q-B",)



def test_short_queue_codes_do_not_replace_dates_or_reminder_numbers(
        monkeypatch,tmp_path):
    from bot import state_read, state_write
    cfg=json.loads(json.dumps(TEST_CONFIG))
    cfg["queue_catalog"]=["1","10"]
    cfg["queue_display_prefix"]=""
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(cfg,ensure_ascii=False))
    path=tmp_path/"state.json"
    state={
        "house:groups":["1","10"],
        "2026-11-21:1":{"digest":"123456","status":"outages"},
        "week:2026-11-21:10":{"digest":"998877","status":"outages"},
        "reminder:2026-11-21:1:360:5":{"sent":True},
        "official:last_id":111,
    }
    state_write(path,state)
    raw=path.read_text(encoding="utf-8")
    assert "2026-11-21" in raw
    assert ":360:5" in raw
    assert '"digest": "123456"' in raw
    assert '"official:last_id": 111' in raw
    assert '"house:groups": [\n    "Q' in raw
    assert '"2026-11-21:1"' not in raw
    assert state_read(path)==state



def test_region_prefix_is_not_misidentified_as_different_city(monkeypatch):
    from event_scope import house_scope_from_secret, coverage_for_house
    config=json.loads(json.dumps(TEST_CONFIG))
    config["other_places"]=["приклад", "іншоград"]
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(config,ensure_ascii=False))
    scope=house_scope_from_secret(address_json())
    status=coverage_for_house(
        "Прикладщина: запроваджено екстрені відключення",scope
    )
    assert status and status["level"]=="region"
    elsewhere=coverage_for_house(
        "Прикладщина: екстрені відключення в місті Іншоград",scope
    )
    assert elsewhere is None



def test_checkpoint_namespace_ignores_json_layout_and_key_order(monkeypatch):
    from power_config import checkpoint_namespace
    first=json.loads(json.dumps(TEST_CONFIG,ensure_ascii=False))
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(first,ensure_ascii=False))
    ns1=checkpoint_namespace(provider_config(),"token-example","-1001")
    alternate=dict(reversed(list(first.items())))
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(
        alternate,ensure_ascii=False,indent=4,
    ))
    ns2=checkpoint_namespace(provider_config(),"token-example","-1001")
    assert ns1==ns2


def test_checkpoint_namespace_changes_for_property_and_destination(monkeypatch):
    from power_config import checkpoint_namespace
    config=json.loads(json.dumps(TEST_CONFIG))
    original=checkpoint_namespace(config,"bot-A","-1001")
    changed=json.loads(json.dumps(config))
    changed["address"]["house"]="88"
    assert checkpoint_namespace(changed,"bot-A","-1001")!=original
    assert checkpoint_namespace(config,"bot-A","-1002")!=original
    # A credential rotation must not be treated as a new destination.
    assert checkpoint_namespace(config,"bot-B","-1001")==original
    assert checkpoint_namespace(config,"bot-A","-1001")==original



@pytest.mark.parametrize("bad_key", [
    "",
    "a" * 64,
    "not-a-random-64-character-hexadecimal-state-key",
    "0000000000000000000000000000000000000000000000000000000000000000",
])
def test_weak_or_invalid_state_key_rejected(monkeypatch, bad_key):
    config=json.loads(json.dumps(TEST_CONFIG))
    config["state_key"]=bad_key
    monkeypatch.setenv("POWER_CONFIG_JSON",json.dumps(config,ensure_ascii=False))
    with pytest.raises(ConfigurationError,match="64 random hexadecimal"):
        provider_config()


def test_equivalent_house_spelling_and_quarter_metadata_do_not_reset_history():
    from power_config import checkpoint_namespace
    cfg = json.loads(json.dumps(TEST_CONFIG, ensure_ascii=False))
    cfg["address"] = {
        "city": "м. Зелений Бір", "street": "вулиця Зразкова",
        "house": "42А", "district": "Умовний район",
        "quarter": "987 квартал",
    }
    baseline = checkpoint_namespace(cfg, "telegram-token", "-1001")
    alias = json.loads(json.dumps(cfg, ensure_ascii=False))
    alias["address"]["street"] = "вул. Зразкова"
    alias["address"]["house"] = "42-A"
    alias["address"]["quarter"] = "інший опис кварталу"
    alias["address"]["district"] = "новий район"
    assert checkpoint_namespace(alias, "new-token", "-1001") == baseline
    for equivalent in ("42 А", "42/А", "42.А", "42-а", "42a"):
        alias["address"]["house"] = equivalent
        assert checkpoint_namespace(alias, "new-token", "-1001") == baseline
    alias["address"]["house"] = "4/2А"
    assert checkpoint_namespace(alias, "new-token", "-1001") != baseline
    alias["address"]["house"] = "42/1"
    assert checkpoint_namespace(alias, "new-token", "-1001") != baseline


def test_list_directory_keeps_multiple_rows_for_one_house():
    parsed = parse_homes({"data": [
        {"num": "42", "sub_type_reason": ["GPV3.1"], "sub_type": "ремонт"},
        {"house_num": "42", "sub_type_reason": ["GPV2.1"]},
    ]})
    assert parsed.houses["42"].group_ids == ["GPV3.1", "GPV2.1"]
    assert parsed.houses["42"].sub_type == "ремонт"


def test_composite_queue_names_and_legacy_keys():
    from bot import _map_state_key
    tokens = {"Z":"Q" + "1"*24, "Z:Північ":"Q" + "2"*24}
    reverse = {v:k for k,v in tokens.items()}
    for prefix, suffix in [("", ""), ("week:", ""), ("reminder:", ":360:15")]:
        start = prefix + "2026-11-22:"
        key = start + "Z:Північ" + suffix
        anonymized = _map_state_key(key, tokens)
        assert anonymized == start + tokens["Z:Північ"] + suffix
        assert _map_state_key(anonymized, reverse) == key
        assert _map_state_key(start + tokens["Z"] + ":Північ" + suffix, reverse) == key


def test_address_config_without_operator_telegram(monkeypatch):
    config = dict(TEST_CONFIG)
    config.pop("telegram_feed")
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(config))
    assert provider_config()["telegram_feed"] == ""
    assert json.loads(address_json()) == TEST_CONFIG["address"]


def test_same_local_date_conflicts_never_erase_a_published_outage():
    raw = {"data": {
        "1900000000": {"A": {"1": "no"}, "B": {"1": "yes"}},
        "1900000001": {"A": {"1": "yes"}, "C": {"1": "no"}},
        "1900000002": {"A": {"1": "yes"}, "B": {"1": "yes"}},
    }}
    assert parse_fact(raw).days == {
        "1900000000": {"A": {}, "B": {"1": "yes"}, "C": {"1": "no"}}}
    with pytest.raises(RuntimeError):
        parse_fact({"data": {"1900000000": "broken"}})
    assert parse_fact({"data": {"1900000000": {"B": None}}}).days == {
        "1900000000": {"B": {}}}


def test_source_link_never_exposes_private_request_path_or_query(monkeypatch):
    from power_config import official_site
    config = {**TEST_CONFIG, "site_url": "https://grid.example.invalid/house/42?unit=7"}
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(config))
    assert official_site() == "https://grid.example.invalid"
    config["public_site_url"] = "https://grid.example.invalid/news"
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(config))
    assert official_site() == config["public_site_url"]


@pytest.mark.parametrize("field", ["GPV3.1", ["GPV3.1"]])
def test_single_queue_in_address_directory(field):
    response = parse_homes({"data": {"12/Б": {"sub_type_reason": field}}})
    assert get_house_entry(response, "12Б").group_ids == ["GPV3.1"]
