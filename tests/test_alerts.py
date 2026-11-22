from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alerts import (
    address_clear_message,
    address_decision,
    address_event,
    address_fields,
    address_message,
    get_house_entry,
    normalized_house,
    parse_provider_date,
    official_posts,
    regional_event,
    region_candidates,
)

KYIV=ZoneInfo("Europe/Kyiv")
NOW=datetime(2026,11,8,16,0,tzinfo=KYIV)
HOUSE_SCOPE={"city":"м. Зелений Бір", "street":"вул. Умовна", "house":"12Г"}


def scoped_candidates(page, state, when):
    return region_candidates(page, state, when, scope=HOUSE_SCOPE)



def fake_post(id, title, date=NOW):
    return {"id":id,"text":title,"date":date,"url":f"https://t.me/sample_energy/{id}"}


def html_page(posts):
    nodes=[]
    for p in posts:
        nodes.append(
            f'<div class="tgme_widget_message" data-post="sample_energy/{p["id"]}">'
            f'<div class="tgme_widget_message_text">{p["text"]}</div>'
            f'<time datetime="{p["date"].isoformat()}">x</time>'
            "</div>"
        )
    return "<html><body>"+"".join(nodes)+"</body></html>"


def house(reason="",kind="",start="",end="",groups=("GPV3.1",)):
    return SimpleNamespace(
        sub_type=reason,outage_type=kind,start_date=start,end_date=end,
        group_ids=list(groups),
    )


def test_official_html_posts_are_parsed_by_id_and_time():
    posts=[fake_post(15,"Прикладщина: графіки"),
           fake_post(16,"Прикладщина: екстрені відключення запроваджено")]
    result=official_posts(html_page(posts))
    assert len(result)==2
    assert result[1]["id"]==16
    assert result[1]["url"]=="https://t.me/sample_energy/16"


def test_kyiv_dnipro_city_and_general_news_not_transmitted_to_kryvyi_rih():
    samples=[
        "‼️ Київ, Київська область та місто Другоград: тривають екстрені відключення",
        "‼️ Другоград: екстрені відключення",
        "‼️ Київ: екстрені відключення запроваджено",
        "Прикладщина: ворог атакує енергооб’єкти ПрикладЕнерго",
        "Прикладщина: графіки відключень на 8 листопада",
        "Прикладщина: відновлення електропостачання у місті Другоград",
    ]
    # Classification alone is not delivery: scope verification must
    # exclude the different city, even for a restoration announcement.
    for title in samples:
        page = html_page([fake_post(21, title)])
        assert not [r for r in scoped_candidates(
            page, {"official:last_id": 20}, NOW
        ) if r[3]], title


def test_emergency_started_and_cancelled():
    active=regional_event(fake_post(31,
        "‼️ Прикладщина: екстрені відключення\n"
        "За командою Укренерго застосовані екстрені відключення."))
    ended=regional_event(fake_post(32,
        "Прикладщина: екстрені відключення скасовано по всій Прикладній області"))
    assert active["kind"]=="emergency" and active["status"]=="active"
    assert ended["status"]=="ended"
    assert active["area"]=="Прикладна область"


def test_local_city_emergency_is_applicable():
    e=regional_event(fake_post(45,"Зелений Бір: запроваджені екстрені відключення"))
    assert e["status"]=="active"
    assert e["area"]=="Зелений Бір"


def test_gav_is_not_confused_with_gpv():
    e=regional_event(fake_post(33,
        "‼️ Прикладщина: запроваджені графіки аварійних відключень"))
    assert e is not None and e["kind"]=="gav"
    assert regional_event(fake_post(34,
        "Прикладщина: графіки погодинних відключень діють")) is None


def test_old_emergency_on_first_run_is_silent_and_seeds_latest():
    state={}
    page=html_page([fake_post(
        10,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області",
        NOW-timedelta(hours=7),
    )])
    messages = [x for x in scoped_candidates(page,state,NOW) if x[3]]
    assert len(messages) == 1
    assert "official:last_id" not in state


def test_first_run_recent_active_emergency_notified_once():
    state={}
    page=html_page([fake_post(
        10,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області",
        NOW-timedelta(minutes=15),
    )])
    results=scoped_candidates(page,state,NOW)
    sends=[x for x in results if x[3]]
    assert len(sends)==1
    assert sends[0][2]["status"]=="active"
    assert "Офіційне повідомлення опубліковано" in sends[0][3]
    assert "не підтверджує відключення кожної квартири" in sends[0][3]
    assert "official:last_id" not in state
    for _id,key,value,_message in results:state[key]=value
    assert not [x for x in scoped_candidates(page,state,NOW) if x[3]]


def test_first_run_recent_emergency_cancelled_does_not_claim_it_is_active():
    state={}
    page=html_page([
        fake_post(10,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області",
                  NOW-timedelta(minutes=30)),
        fake_post(11,"Прикладщина: екстрені відключення скасовано по всій Прикладній області",
                  NOW-timedelta(minutes=5)),
    ])
    assert scoped_candidates(page,state,NOW)==[]
    assert state["official:last_id"]==11


def test_recent_active_region_not_confused_with_actual_building_outage():
    event=regional_event(fake_post(
        12,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області",
        NOW-timedelta(minutes=4),
    ))
    from alerts import regional_message
    assert "не підтверджує відключення кожної квартири" in regional_message(event)


def test_new_status_once_and_repeated_status_silent():
    state={"official:last_id":10}
    first=html_page([fake_post(11,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області")])
    result=scoped_candidates(first,state,NOW)
    assert len([r for r in result if r[3]])==1
    assert state["official:last_id"]==10  # checkpoint must follow a successful send
    for post_id,key,value,text in result:
        state[key]=value
    repeat=html_page([fake_post(12,"Прикладщина: екстрені відключення тривають по всій Прикладній області")])
    result=scoped_candidates(repeat,state,NOW)
    assert not [r for r in result if r[3]]


def test_start_and_end_in_same_batch_are_silent_if_never_announced():
    state={"official:last_id":10}
    page=html_page([
        fake_post(11,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області"),
        fake_post(12,"Прикладщина: екстрені відключення скасовано по всій Прикладній області"),
    ])
    output=scoped_candidates(page,state,NOW)
    assert not [r for r in output if r[3]]
    assert output[-1][1]=="official:last_id"


def test_cancel_without_prior_announcement_is_silent():
    state={"official:last_id":10}
    page=html_page([fake_post(11,"Прикладщина: екстрені відключення скасовано по всій Прикладній області")])
    output=scoped_candidates(page,state,NOW)
    assert not [r for r in output if r[3]]


def test_stale_news_cannot_trigger_notifications():
    state={"official:last_id":10}
    page=html_page([fake_post(11,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області",
                    NOW-timedelta(hours=9))])
    assert len([r for r in scoped_candidates(page,state,NOW) if r[3]]) == 1


def test_house_normalization_and_private_address_validation():
    assert normalized_house("12/Г") != normalized_house("12Г")
    assert address_fields('{"city":"місто", "street":"вулиця", "house":"12/Г"}')==[
        "місто","вулиця","12/Г"
    ]
    try:
        address_fields('{"city":"місто"}')
    except RuntimeError:
        pass
    else:
        raise AssertionError("Unusable secret should fail")


def test_exact_house_selection_without_guessing():
    response=SimpleNamespace(houses={"12/Г":house(), "12/Е":house()})
    assert get_house_entry(response,"12/Г") is response.houses["12/Г"]
    try:
        get_house_entry(response,"12/Д")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Unknown house must not be guessed")


def test_planned_repair_and_local_fault_distinguished():
    repair=address_event(house(reason="Планові роботи",kind="1",
          start="08.11.2026 16:00",end="08.11.2026 20:00"))
    fault=address_event(house(reason="Аварійне відключення",kind="2",
          start="08.11.2026 16:00",end="08.11.2026 20:00"))
    other=address_event(house(reason="Невідома причина",kind="2"))
    assert repair["category"]=="repair"
    assert fault["category"]=="fault"
    assert other["category"]=="unclassified"
    assert "Планові ремонтні роботи" in address_message(repair)
    assert "Аварійне відключення" in address_message(fault)


def test_no_initial_address_outage_is_silent():
    state={}
    changes=address_decision(house(),state,NOW)
    assert all(x[3] is None for x in changes)
    assert any(x[1]=="address:groups" for x in changes)


def test_new_local_outage_notified_once():
    event=house(reason="Планові роботи",kind="1",
                start="08.11.2026 17:00",end="08.11.2026 20:00")
    state={}
    first=address_decision(event,state,NOW)
    assert len([x for x in first if x[3]])==1
    for _,key,value,_ in first:state[key]=value
    assert not [x for x in address_decision(event,state,NOW) if x[3]]


def test_early_clear_vs_natural_end():
    old={"status":"active","event":{
        "category":"repair","reason":"Планові роботи",
        "start":"08.11.2026 17:00","end":"08.11.2026 20:00"}}
    msg=address_clear_message(old["event"],NOW)
    assert msg and "не відображається" in msg
    assert address_clear_message(old["event"],NOW+timedelta(hours=5)) is None
    state={"address:last_outage":old,"address:groups":["GPV3.1"]}
    changes=address_decision(house(),state,NOW)
    assert not [r for r in changes if r[3]]  # missing API data is not an official cancellation


def test_change_of_house_group_only_after_baseline():
    state={"address:groups":["GPV3.1"]}
    result=address_decision(house(groups=["GPV5.1"]),state,NOW)
    assert any("змінив чергу" in x[3] for x in result if x[3])
    assert all("Умовна_вулиця" not in x[3] for x in result if x[3])


def test_public_state_never_contains_address_response_reason():
    item=house(reason="Планові роботи за приватною адресою 12/Г",kind="1",
               start="08.11.2026 17:00",end="08.11.2026 20:00")
    state={}
    result=address_decision(item,state,NOW)
    alerts=[x for x in result if x[1]=="address:last_outage"]
    assert len(alerts)==1
    data=alerts[0][2]
    assert "event" not in data
    assert "reason" not in data
    assert "address" not in data
    assert "12/Г" not in str(data)
    assert "12/Г" not in str(alerts[0][3])
    assert "Причина: планові роботи" in alerts[0][3]


def test_first_verified_queue_does_not_invent_prior_assignment():
    state={}
    result=address_decision(house(groups=["GPV5.1"]),state,NOW)
    assert not [x for x in result if x[1]=="address:groups" and x[3]]


def test_reason_wording_only_does_not_alert_again():
    state={}
    a=house(reason="Планові роботи",kind="1",
            start="08.11.2026 17:00",end="08.11.2026 20:00")
    b=house(reason="Планові роботи з технічного обслуговування",kind="1",
            start="08.11.2026 17:00",end="08.11.2026 20:00")
    first=address_decision(a,state,NOW)
    for _,key,value,_ in first:
        state[key]=value
    follow=address_decision(b,state,NOW)
    assert not [item for item in follow if item[3]]


def test_address_outage_discovered_after_started_time_explains_late_information():
    item=house(reason="Аварійне відключення",kind="2",
               start="08.11.2026 15:00",end="08.11.2026 21:00")
    result=address_decision(item,{},NOW)
    notice=next(x[3] for x in result if x[1]=="address:last_outage")
    assert "час початку вже минув" in notice
    assert "початися до отримання" in notice
    assert "Інформацію виявлено" in notice
    assert "не вимірює фактичну" in notice
    assert "21:00" in notice
    assert "Вулиця_Зразок" not in notice


def test_future_address_outage_does_not_claim_it_already_started():
    item=house(reason="Планові роботи",kind="1",
               start="08.11.2026 18:00",end="08.11.2026 20:00")
    result=address_decision(item,{},NOW)
    notice=next(x[3] for x in result if x[1]=="address:last_outage")
    assert "час початку вже минув" not in notice
    assert "Зазначений початок: 08.11.2026 18:00" in notice


def test_unknown_address_restoration_time_explicit():
    item=house(reason="Аварійне відключення",kind="2",
               start="08.11.2026 15:00")
    result=address_decision(item,{},NOW)
    notice=next(x[3] for x in result if x[1]=="address:last_outage")
    assert "Орієнтовний час відновлення не зазначено" in notice



def test_actual_provider_alternate_clock_first_format_detected_correctly():
    assert parse_provider_date("15:00 08.11.2026")==parse_provider_date("08.11.2026 15:00")
    item=house(reason="Аварійні роботи",kind="2",
               start="15:00 08.11.2026",end="21:00 08.11.2026")
    result=address_decision(item,{},NOW)
    notice=next(x[3] for x in result if x[1]=="address:last_outage")
    assert "час початку вже минув" in notice
    assert "21:00 08.11.2026" in notice



def test_generic_regional_emergency_is_relevant_but_house_status_unknown():
    """Even partial restrictions in a containing region justify a cautious alert."""
    state={"official:last_id":10}
    post=html_page([fake_post(11,
        "Прикладщина: запроваджено екстрені відключення")])
    rows=scoped_candidates(post,state,NOW)
    notices=[r[3] for r in rows if r[3]]
    assert len(notices)==1
    assert "Прикладна область" in notices[0]
    assert "Фактичну наявність електроенергії у будинку не підтверджено" in notices[0]



def test_exact_home_scoped_emergency_message_is_sent():
    state={"official:last_id":10}
    post=html_page([fake_post(11,
        "Зелений Бір: запроваджено екстрені відключення. "
        "вул. Умовна, буд. 12Г")])
    rows=scoped_candidates(post,state,NOW)
    assert len([r for r in rows if r[3]])==1


def test_emergency_elsewhere_is_ignored_even_when_entire_area():
    state={"official:last_id":10}
    post=html_page([fake_post(11,
        "Київ: екстрені відключення введено по всьому місту Києву")])
    rows=scoped_candidates(post,state,NOW)
    assert not [r for r in rows if r[3]]


def test_late_provider_announcement_recovered_at_next_check():
    state={"official:last_id":10}
    post=html_page([fake_post(11,
        "Прикладщина: запроваджено екстрені відключення "
        "по всій Прикладній області",
        NOW-timedelta(hours=5))])
    rows=scoped_candidates(post,state,NOW)
    assert len([r for r in rows if r[3]])==1



def test_partial_pokrovskyi_district_notice_sent_only_with_private_scope():
    from alerts import region_candidates
    private_scope={**HOUSE_SCOPE,"district":"Зразковий район","quarter":"987 квартал"}
    page=html_page([fake_post(11,
        "Зелений Бір: частково знеструмлено Зразковий район")])
    narrow=region_candidates(page,{"official:last_id":10},NOW,scope=private_scope)
    notes=[r[3] for r in narrow if r[3]]
    assert len(notes)==1
    assert "часткове знеструмлення" in notes[0]
    assert "Невідомо, чи зачепило відключення конкретний будинок" in notes[0]
    broad=region_candidates(page,{"official:last_id":10},NOW,scope=HOUSE_SCOPE)
    assert not [r for r in broad if r[3]]


def test_partial_quarter_notice_sent_only_for_matching_private_quarter():
    from alerts import region_candidates
    private_scope={**HOUSE_SCOPE,"district":"Зразковий район","quarter":"987 квартал"}
    page=html_page([fake_post(11,
        "Зелений Бір: частково знеструмлено 987 квартал")])
    matches=region_candidates(page,{"official:last_id":10},NOW,scope=private_scope)
    assert len([r for r in matches if r[3]])==1
    miss=region_candidates(page,{"official:last_id":10},NOW,
        scope={**private_scope,"quarter":"130 квартал"})
    assert not [r for r in miss if r[3]]


def test_partial_area_outage_report_detected_as_distinct_event():
    event=regional_event(fake_post(13,
        "Прикладщина: частково знеструмлено область"))
    assert event and event["kind"]=="area" and event["status"]=="active"


def test_different_city_inside_the_same_oblast_is_silent():
    page=html_page([fake_post(14,
        "Прикладщина: екстрені відключення лише у місті Іншоград")])
    assert not [r for r in scoped_candidates(page,{"official:last_id":10},NOW) if r[3]]



def test_partial_area_restoration_notified_only_after_prior_area_alert():
    scope={**HOUSE_SCOPE,"district":"Зразковий район","quarter":"987 квартал"}
    first=html_page([fake_post(11,
        "Зелений Бір: частково знеструмлено Зразковий район")])
    state={"official:last_id":10}
    opened=region_candidates(first,state,NOW,scope=scope)
    first_messages=[r[3] for r in opened if r[3]]
    assert len(first_messages)==1
    for _id,key,value,_message in opened:
        state[key]=value
    restored=html_page([fake_post(12,
        "Зелений Бір: відновлено електропостачання у Зразковому районі")])
    ended=region_candidates(restored,state,NOW,scope=scope)
    sent=[r[3] for r in ended if r[3]]
    assert len(sent)==1
    assert "відновлення електропостачання на території" in sent[0]
    assert "не підтверджує наявність електроенергії у кожному будинку" in sent[0]
    for _id,key,value,_message in ended:state[key]=value
    assert not [r for r in region_candidates(restored,state,NOW,scope=scope) if r[3]]


def test_partial_area_restoration_without_prior_event_is_silent():
    scope={**HOUSE_SCOPE,"district":"Зразковий район","quarter":"987 квартал"}
    page=html_page([fake_post(15,
        "Зелений Бір: відновлено електропостачання у Зразковому районі")])
    rows=region_candidates(page,{"official:last_id":14},NOW,scope=scope)
    assert not [r for r in rows if r[3]]



def test_address_first_discovered_after_end_is_silent_and_deduplicated():
    event=house(reason="Аварійні роботи",kind="2",
                start="08.11.2026 12:00",end="08.11.2026 15:00")
    for tested_at in (NOW,NOW+timedelta(minutes=1)):
        rows=address_decision(event,{},tested_at)
        assert not [x for x in rows if x[1]=="address:last_outage" and x[3]]


def test_address_end_exactly_now_is_silent():
    event=house(reason="Аварійні роботи",kind="2",
                start="08.11.2026 12:00",end="08.11.2026 16:00")
    rows=address_decision(event,{},NOW)
    assert not [x for x in rows if x[1]=="address:last_outage" and x[3]]


def test_existing_ongoing_outage_ending_early_notifies_cancel():
    old={"address:last_outage":{
        "status":"active","digest":"old","end":"08.11.2026 18:00"
    }}
    event=house(reason="Аварійні роботи",kind="2",
                start="08.11.2026 12:00",end="08.11.2026 15:00")
    rows=address_decision(event,old,NOW)
    msg=next(x[3] for x in rows if x[1]=="address:last_outage")
    assert "уточнив орієнтовний час" in msg
    assert "не підтверджено" in msg


def test_official_emergency_ended_before_first_observation_is_silent():
    page=html_page([
        fake_post(20,"Прикладщина: запроваджені екстрені відключення по всій Прикладній області",
                  NOW-timedelta(minutes=30)),
        fake_post(21,"Прикладщина: екстрені відключення скасовано по всій Прикладній області",
                  NOW-timedelta(minutes=8)),
    ])
    rows=scoped_candidates(page,{"official:last_id":19},NOW)
    assert not [r for r in rows if r[3]]


def test_official_emergency_end_still_notifies_if_started_was_previously_sent():
    page=html_page([fake_post(21,
         "Прикладщина: екстрені відключення скасовано по всій Прикладній області",
         NOW-timedelta(minutes=8))])
    state={"official:last_id":20,"official:status:emergency:region":"active"}
    rows=scoped_candidates(page,state,NOW)
    ends=[r for r in rows if r[3]]
    assert len(ends)==1
    assert ends[0][2]["status"]=="ended"


def test_post_with_explicitly_elapsed_end_never_starts_outage_notice():
    page=html_page([fake_post(20,
        "Прикладщина: запроваджені екстрені відключення по всій Прикладній області. "
        "Орієнтовне завершення: 08.11.2026 15:00",NOW-timedelta(minutes=4))])
    rows=scoped_candidates(page,{"official:last_id":19},NOW)
    assert not [r for r in rows if r[3]]


def test_post_with_future_explicit_end_is_not_suppressed():
    page=html_page([fake_post(20,
        "Прикладщина: запроваджені екстрені відключення по всій Прикладній області. "
        "Орієнтовне завершення: 08.11.2026 18:00",NOW-timedelta(minutes=4))])
    rows=scoped_candidates(page,{"official:last_id":19},NOW)
    assert len([r for r in rows if r[3]])==1


def test_direct_ukrenergo_order_without_colon_is_parsed():
    page = html_page([fake_post(20,
        "За командою Укренерго на Прикладщині запроваджено "
        "екстрені відключення")])
    notices = [x for x in scoped_candidates(
        page, {"official:last_id": 19}, NOW) if x[3]]
    assert len(notices) == 1
    assert "Запроваджено екстрені" in notices[0][3]


def test_older_post_with_future_official_end_is_relevant():
    page = html_page([fake_post(22,
        "Прикладщина: запроваджено екстрені відключення. "
        "Орієнтовне завершення: 08.11.2026 19:00",
        NOW - timedelta(hours=9))])
    notices = [x for x in scoped_candidates(
        page, {"official:last_id": 19}, NOW) if x[3]]
    assert len(notices) == 1
    assert datetime.fromisoformat(notices[0][2]["end"]).hour == 19


def test_official_repeated_active_with_revised_end_notified():
    state = {"official:last_id": 10}
    first = html_page([fake_post(11,
        "Прикладщина: запроваджено екстрені відключення. "
        "Орієнтовне завершення: 08.11.2026 18:00")])
    for _id, key, value, _message in scoped_candidates(first, state, NOW):
        state[key] = value
    changed = html_page([fake_post(12,
        "Прикладщина: екстрені відключення тривають. "
        "Орієнтовне завершення: 08.11.2026 20:00")])
    notices = [x for x in scoped_candidates(changed, state, NOW) if x[3]]
    assert len(notices) == 1
    assert "уточнив строки" in notices[0][3]
    for _id, key, value, _message in notices:
        state[key] = value
    state["official:last_id"] = 12
    assert not [x for x in scoped_candidates(changed, state, NOW) if x[3]]


def test_regional_record_tracks_only_public_metadata():
    page = html_page([fake_post(21,
        "Прикладщина: запроваджено екстрені відключення")])
    message = next(x for x in scoped_candidates(
        page, {"official:last_id": 20}, NOW) if x[3])
    record = message[2]
    assert {"source", "published", "detected", "start",
            "end", "status", "event_id"} <= set(record)
    assert "operator" not in record and "area" not in record
    assert "t.me" not in str(record)
    assert "Умовна" not in str(record) and "12Г" not in str(record)


def test_missing_address_entry_is_not_early_cancellation():
    state = {"address:last_outage": {
        "status": "active", "end": "08.11.2026 19:00",
        "digest": "old",
    }}
    entries = (house(), None)
    for entry in entries:
        if entry is None:
            assert address_decision(entry, state, NOW) == []
        else:
            assert not [x for x in address_decision(entry, state, NOW) if x[3]]


def test_address_date_only_end_is_not_treated_as_expired():
    current = house(reason="Аварійні роботи", kind="2",
                    start="08.11.2026 10:00", end="08.11.2026")
    rows = address_decision(current, {}, NOW)
    assert any(x[1] == "address:last_outage" and x[3] for x in rows)


def test_house_number_separators_not_confused():
    assert normalized_house("1/1") != normalized_house("11")
    assert normalized_house("42-А") == normalized_house("42А")
    assert normalized_house("42/А") != normalized_house("42А")


def test_regional_transition_to_gpv_ends_emergency():
    e = regional_event(fake_post(
        101, "Прикладщина: за командою Укренерго "
             "переходимо до стабілізаційних графіків відключень"
    ))
    assert e and e["kind"] == "emergency" and e["status"] == "ended"


def test_two_distinct_official_activations_in_one_batch():
    page = html_page([
        fake_post(201, "Прикладщина: запроваджено екстрені відключення "
                       "по всій Прикладній області"),
        fake_post(202, "Прикладщина: введено нові екстрені відключення "
                       "по всій Прикладній області"),
    ])
    messages = [item for item in scoped_candidates(
        page, {"official:last_id": 200}, NOW
    ) if item[3]]
    assert len(messages) == 2
    assert {x[2]["post_id"] for x in messages} == {201, 202}


def test_edited_official_post_detected_even_with_same_post_id():
    page = html_page([fake_post(
        210, "Прикладщина: запроваджено екстрені відключення. "
             "Орієнтовне завершення: 08.11.2026 18:00")])
    state = {"official:last_id": 209}
    for _id, key, value, _message in scoped_candidates(page, state, NOW):
        state[key] = value
    changed = html_page([fake_post(
        210, "Прикладщина: запроваджено екстрені відключення. "
             "Орієнтовне завершення: 08.11.2026 19:00")])
    messages = [x for x in scoped_candidates(changed, state, NOW) if x[3]]
    assert len(messages) == 1
    assert messages[0][2]["end"].endswith("19:00:00+02:00")


def test_missing_revised_end_is_not_an_end_time_change():
    first = html_page([fake_post(
        230, "Прикладщина: запроваджено екстрені відключення. "
             "Орієнтовне завершення: 08.11.2026 19:00")])
    state = {"official:last_id": 229}
    for _id, key, value, _message in scoped_candidates(first, state, NOW):
        state[key] = value
    second = html_page([fake_post(
        231, "Прикладщина: екстрені відключення тривають")])
    assert not [x for x in scoped_candidates(second, state, NOW) if x[3]]


def test_real_older_unended_report_is_not_suppressed_six_hours_later():
    page = html_page([fake_post(
        232, "Прикладщина: запроваджено екстрені відключення",
        NOW - timedelta(hours=9))])
    assert len([x for x in scoped_candidates(
        page, {"official:last_id": 231}, NOW) if x[3]]) == 1


def test_transition_from_emergency_to_gpv_closes_prior_status():
    active = {"official:last_id": 240, "official:status:emergency:region": "active"}
    page = html_page([fake_post(
        241, "Прикладщина: за командою Укренерго "
             "переходимо до стабілізаційних графіків відключень")])
    notices = [x for x in scoped_candidates(page, active, NOW) if x[3]]
    assert len(notices) == 1
    assert notices[0][2]["status"] == "ended"


def test_edit_of_prior_official_post_without_time_still_reported():
    state = {"official:last_id": 300}
    first = html_page([fake_post(
        301, "Прикладщина: запроваджено екстрені відключення "
             "на території Прикладної області")])
    for _pid, key, value, _msg in scoped_candidates(first, state, NOW):
        state[key] = value
    revised = html_page([fake_post(
        301, "Прикладщина: запроваджено екстрені відключення "
             "по всій території Прикладної області")])
    messages = [x[3] for x in scoped_candidates(revised, state, NOW) if x[3]]
    assert len(messages) == 1
    assert "змінив офіційне повідомлення" in messages[0].casefold()


def test_untrusted_hash_state_does_not_crash_monitor():
    state = {"official:last_id": 400,
             "official:post_hashes": {"invalid": "hash"}}
    page = html_page([fake_post(
        401, "Прикладщина: екстрені відключення запроваджено")])
    assert [row for row in scoped_candidates(page, state, NOW) if row[3]]


def test_private_house_lookup_failure_logs_only_counts(caplog):
    import logging
    from alerts import get_house_entry
    response = SimpleNamespace(houses={"1/1": house(), "12": house()})
    with caplog.at_level(logging.WARNING):
        try:
            get_house_entry(response, "11")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Must refuse approximate building")
    assert "записів=2" in caplog.text
    assert "приблизних збігів=1" in caplog.text
    assert "11" not in caplog.text
    assert "1/1" not in caplog.text


def test_letter_suffix_separator_is_a_unique_alias_only():
    from alerts import get_house_entry
    record = house(groups=("GPV3.1",))
    response = SimpleNamespace(houses={"42/А": record})
    assert get_house_entry(response, "42А") is record
    assert get_house_entry(response, "42-А") is record


def test_numeric_slash_does_not_become_house_eleven():
    from alerts import get_house_entry
    response = SimpleNamespace(houses={"1/1": house()})
    try:
        get_house_entry(response,"11")
    except RuntimeError:
        pass
    else:
        raise AssertionError("House 1/1 cannot be silently treated as 11")


def test_ambiguous_letter_suffix_alias_is_rejected():
    from alerts import get_house_entry
    response = SimpleNamespace(houses={"42/А": house(), "42.А": house()})
    try:
        get_house_entry(response,"42А")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Two approximate variants cannot be resolved")


def test_gop_capacity_limits_do_not_trigger_false_household_outage():
    general = fake_post(
        600, "Прикладщина: введено ГОП для промислових споживачів"
    )
    event = regional_event(general)
    assert event is not None
    assert event["kind"] == "gop"
    assert event["status"] == "active"
    # Province-level industrial power limits are not evidence of outages
    # for a residential building inside that province.
    notices = scoped_candidates(
        html_page([general]), {"official:last_id":599}, NOW
    )
    assert not [item for item in notices if item[3]]


def test_gop_only_reports_a_specific_officially_named_house():
    post = fake_post(
        601, "Зелений Бір: введено ГОП за адресою вул. Умовна 12Г"
    )
    notices = [item for item in scoped_candidates(
        html_page([post]), {"official:last_id":600}, NOW
    ) if item[3]]
    assert len(notices) == 1
    assert notices[0][2]["status"] == "active"
    assert "Обмеження електричної потужності" in notices[0][3]
    assert "не графік відключення світла" in notices[0][3]


def test_independent_dated_emergencies_are_not_conflated():
    """Each official start is a separate active event with its own closure."""
    state = {"official:last_id": 700}
    first = html_page([
        fake_post(701, "Прикладщина: запроваджено екстрені відключення. "
                        "Початок: 08.11.2026 14:00"),
        fake_post(702, "Прикладщина: введено нові екстрені відключення. "
                        "Початок: 08.11.2026 15:00"),
    ])
    opened = scoped_candidates(first, state, NOW)
    delivered = [row for row in opened if row[3]]
    assert len(delivered) == 2
    keys = {row[1] for row in delivered}
    assert len(keys) == 2
    assert all(key.startswith("official:incident:emergency:region:")
               for key in keys)
    for _, key, record, _ in opened:
        state[key] = record

    cancelled = html_page([
        fake_post(703, "Прикладщина: екстрені відключення скасовано. "
                        "Початок: 08.11.2026 14:00"),
    ])
    rows = scoped_candidates(cancelled, state, NOW)
    notices = [row for row in rows if row[3]]
    assert len(notices) == 1
    assert notices[0][1] in keys
    assert notices[0][2]["status"] == "ended"
    for _, key, record, _ in rows:
        state[key] = record
    remaining = [key for key in keys if state[key]["status"] == "active"]
    assert len(remaining) == 1

    # An undated closure is now unambiguous: exactly one incident remains.
    final = html_page([
        fake_post(704, "Прикладщина: екстрені відключення скасовано"),
    ])
    final_notices = [row for row in scoped_candidates(final, state, NOW) if row[3]]
    assert len(final_notices) == 1
    assert final_notices[0][1] == remaining[0]


def test_ambiguous_undated_closure_preserves_other_official_notices():
    state = {"official:last_id": 710}
    opened = scoped_candidates(html_page([
        fake_post(711, "Прикладщина: запроваджено екстрені відключення. "
                        "Початок: 08.11.2026 13:00"),
        fake_post(712, "Прикладщина: запроваджено екстрені відключення. "
                        "Початок: 08.11.2026 14:00"),
    ]), state, NOW)
    for _, key, record, _ in opened:
        state[key] = record

    rows = scoped_candidates(html_page([
        fake_post(713, "Прикладщина: екстрені відключення скасовано"),
        fake_post(714, "Прикладщина: частково знеструмлено територію області"),
    ]), state, NOW)
    messages = [item for item in rows if item[3]]
    assert len(messages) == 2
    unresolved = next(item for item in messages
                      if item[1].startswith("official:unresolved:"))
    assert "не скасовує жодну" in unresolved[3]
    assert "не підтверджує відновлення світла" in unresolved[3]
    assert any(item[1].startswith("official:status:area:") for item in messages)
    assert sum(
        isinstance(v, dict) and v.get("status") == "active"
        for k, v in state.items() if k.startswith("official:incident:")
    ) == 2


def test_dated_active_then_ended_in_same_batch_is_not_late_news():
    state = {"official:last_id": 720}
    notices = scoped_candidates(html_page([
        fake_post(721, "Прикладщина: запроваджено екстрені відключення. "
                        "Початок: 08.11.2026 14:00"),
        fake_post(722, "Прикладщина: екстрені відключення скасовано. "
                        "Початок: 08.11.2026 14:00"),
    ]), state, NOW)
    assert not [row for row in notices if row[3]]


def test_unplanned_repairs_are_not_reported_as_planned():
    assert address_event(house(reason="Аварійний ремонт"))["category"] == "fault"
    for reason in ("Позапланові роботи", "Ремонт кабелю"):
        assert address_event(house(reason=reason))["category"] == "unclassified"


def test_date_only_end_keeps_active_record_after_disappearance():
    current = house(reason="Аварійні роботи", start="08.11.2026 10:00",
                    end="08.11.2026")
    rows = address_decision(current, {}, NOW)
    assert any("час початку вже минув" in (row[3] or "") for row in rows)
    state = {key: value for _, key, value, _ in rows}
    assert not address_decision(house(), state, NOW)
