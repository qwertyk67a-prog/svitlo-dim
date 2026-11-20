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
        assert "кривий ріг" not in text
        assert "дніпропетровщина" not in text
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
    changed["state_key"]="a completely different 32-plus character test key"
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
