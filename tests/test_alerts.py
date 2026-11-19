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
    parse_dtek_date,
    official_posts,
    regional_event,
    region_candidates,
)

KYIV=ZoneInfo("Europe/Kyiv")
NOW=datetime(2026,11,8,16,0,tzinfo=KYIV)
HOUSE_SCOPE={"city":"м. Кривий Ріг", "street":"вул. Умовна", "house":"12Г"}


def scoped_candidates(page, state, when):
    return region_candidates(page, state, when, scope=HOUSE_SCOPE)



def fake_post(id, title, date=NOW):
    return {"id":id,"text":title,"date":date,"url":f"https://t.me/dtek_ua/{id}"}


def html_page(posts):
    nodes=[]
    for p in posts:
        nodes.append(
            f'<div class="tgme_widget_message" data-post="dtek_ua/{p["id"]}">'
            f'<div class="tgme_widget_message_text">{p["text"]}</div>'
            f'<time datetime="{p["date"].isoformat()}">x</time>'
            "</div>"
        )
    return "<html><body>"+"".join(nodes)+"</body></html>"


def house(reason="",kind="",start="",end="",groups=("GPV6.2",)):
    return SimpleNamespace(
        sub_type=reason,outage_type=kind,start_date=start,end_date=end,
        group_ids=list(groups),
    )


def test_official_html_posts_are_parsed_by_id_and_time():
    posts=[fake_post(15,"Дніпропетровщина: графіки"),
           fake_post(16,"Дніпропетровщина: екстрені відключення запроваджено")]
    result=official_posts(html_page(posts))
    assert len(result)==2
    assert result[1]["id"]==16
    assert result[1]["url"]=="https://t.me/dtek_ua/16"


def test_kyiv_dnipro_city_and_general_news_not_transmitted_to_kryvyi_rih():
    samples=[
        "‼️ Київ, Київська область та місто Дніпро: тривають екстрені відключення",
        "‼️ Дніпро: екстрені відключення",
        "‼️ Київ: екстрені відключення запроваджено",
        "Дніпропетровщина: ворог атакує енергооб’єкти ДТЕК",
        "Дніпропетровщина: графіки відключень на 8 листопада",
        "Дніпропетровщина: відновлення електропостачання у місті Дніпро",
    ]
    assert all(regional_event(fake_post(21, t)) is None for t in samples)


def test_emergency_started_and_cancelled():
    active=regional_event(fake_post(31,
        "‼️ Дніпропетровщина: екстрені відключення\n"
        "За командою Укренерго застосовані екстрені відключення."))
    ended=regional_event(fake_post(32,
        "Дніпропетровщина: екстрені відключення скасовано по всій Дніпропетровській області"))
    assert active["kind"]=="emergency" and active["status"]=="active"
    assert ended["status"]=="ended"
    assert active["area"]=="Дніпропетровська область"


def test_local_city_emergency_is_applicable():
    e=regional_event(fake_post(45,"Кривий Ріг: запроваджені екстрені відключення"))
    assert e["status"]=="active"
    assert e["area"]=="Кривий Ріг"


def test_gav_is_not_confused_with_gpv():
    e=regional_event(fake_post(33,
        "‼️ Дніпропетровщина: запроваджені графіки аварійних відключень"))
    assert e is not None and e["kind"]=="gav"
    assert regional_event(fake_post(34,
        "Дніпропетровщина: графіки погодинних відключень діють")) is None


def test_old_emergency_on_first_run_is_silent_and_seeds_latest():
    state={}
    page=html_page([fake_post(
        10,"Дніпропетровщина: запроваджені екстрені відключення по всій Дніпропетровській області",
        NOW-timedelta(hours=7),
    )])
    assert scoped_candidates(page,state,NOW)==[]
    assert state["official:last_id"]==10


def test_first_run_recent_active_emergency_notified_once():
    state={}
    page=html_page([fake_post(
        10,"Дніпропетровщина: запроваджені екстрені відключення по всій Дніпропетровській області",
        NOW-timedelta(minutes=15),
    )])
    results=scoped_candidates(page,state,NOW)
    sends=[x for x in results if x[3]]
    assert len(sends)==1
    assert sends[0][2]=="active"
    assert "Офіційне повідомлення опубліковано" in sends[0][3]
    assert "не підтверджує відключення кожної квартири" in sends[0][3]
    assert "official:last_id" not in state
    for _id,key,value,_message in results:state[key]=value
    assert not [x for x in scoped_candidates(page,state,NOW) if x[3]]


def test_first_run_recent_emergency_cancelled_does_not_claim_it_is_active():
    state={}
    page=html_page([
        fake_post(10,"Дніпропетровщина: запроваджені екстрені відключення по всій Дніпропетровській області",
                  NOW-timedelta(minutes=30)),
        fake_post(11,"Дніпропетровщина: екстрені відключення скасовано по всій Дніпропетровській області",
                  NOW-timedelta(minutes=5)),
    ])
    assert scoped_candidates(page,state,NOW)==[]
    assert state["official:last_id"]==11


def test_recent_active_region_not_confused_with_actual_building_outage():
    event=regional_event(fake_post(
        12,"Дніпропетровщина: запроваджені екстрені відключення по всій Дніпропетровській області",
        NOW-timedelta(minutes=4),
    ))
    from alerts import regional_message
    assert "не підтверджує відключення кожної квартири" in regional_message(event)


def test_new_status_once_and_repeated_status_silent():
    state={"official:last_id":10}
    first=html_page([fake_post(11,"Дніпропетровщина: запроваджені екстрені відключення по всій Дніпропетровській області")])
    result=scoped_candidates(first,state,NOW)
    assert len([r for r in result if r[3]])==1
    assert state["official:last_id"]==10  # checkpoint must follow a successful send
    for post_id,key,value,text in result:
        state[key]=value
    repeat=html_page([fake_post(12,"Дніпропетровщина: екстрені відключення тривають по всій Дніпропетровській області")])
    result=scoped_candidates(repeat,state,NOW)
    assert not [r for r in result if r[3]]


def test_start_and_end_in_same_batch_remain_two_transitions():
    state={"official:last_id":10}
    page=html_page([
        fake_post(11,"Дніпропетровщина: запроваджені екстрені відключення по всій Дніпропетровській області"),
        fake_post(12,"Дніпропетровщина: екстрені відключення скасовано по всій Дніпропетровській області"),
    ])
    output=scoped_candidates(page,state,NOW)
    assert [r[2] for r in output if r[3]]==["active","ended"]


def test_cancel_without_prior_announcement_is_silent():
    state={"official:last_id":10}
    page=html_page([fake_post(11,"Дніпропетровщина: екстрені відключення скасовано по всій Дніпропетровській області")])
    output=scoped_candidates(page,state,NOW)
    assert not [r for r in output if r[3]]


def test_stale_news_cannot_trigger_notifications():
    state={"official:last_id":10}
    page=html_page([fake_post(11,"Дніпропетровщина: запроваджені екстрені відключення по всій Дніпропетровській області",
                    NOW-timedelta(hours=9))])
    assert not [r for r in scoped_candidates(page,state,NOW) if r[3]]


def test_house_normalization_and_private_address_validation():
    assert normalized_house("12/Г")==normalized_house("12Г")
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
    assert get_house_entry(response,"12Г") is response.houses["12/Г"]
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
    state={"address:last_outage":old,"address:groups":["GPV6.2"]}
    changes=address_decision(house(),state,NOW)
    assert len([r for r in changes if r[3]])==1


def test_change_of_house_group_only_after_baseline():
    state={"address:groups":["GPV6.2"]}
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


def test_group_mismatch_at_first_verified_read_gets_warning():
    state={}
    result=address_decision(house(groups=["GPV5.1"]),state,NOW)
    assert any(x[1]=="address:groups" and x[3] for x in result)


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
    assert "Кропивницького" not in notice


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



def test_actual_dtek_alternate_clock_first_format_detected_correctly():
    assert parse_dtek_date("15:00 08.11.2026")==parse_dtek_date("08.11.2026 15:00")
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
        "Дніпропетровщина: запроваджено екстрені відключення")])
    rows=scoped_candidates(post,state,NOW)
    notices=[r[3] for r in rows if r[3]]
    assert len(notices)==1
    assert "Дніпропетровська область" in notices[0]
    assert "Фактичну наявність електроенергії у будинку не підтверджено" in notices[0]



def test_exact_home_scoped_emergency_message_is_sent():
    state={"official:last_id":10}
    post=html_page([fake_post(11,
        "Кривий Ріг: запроваджено екстрені відключення. "
        "вул. Умовна, буд. 12Г")])
    rows=scoped_candidates(post,state,NOW)
    assert len([r for r in rows if r[3]])==1


def test_emergency_elsewhere_is_ignored_even_when_entire_area():
    state={"official:last_id":10}
    post=html_page([fake_post(11,
        "Київ: екстрені відключення введено по всьому місту Києву")])
    rows=scoped_candidates(post,state,NOW)
    assert not [r for r in rows if r[3]]


def test_late_dtek_announcement_recovered_at_next_check():
    state={"official:last_id":10}
    post=html_page([fake_post(11,
        "Дніпропетровщина: запроваджено екстрені відключення "
        "по всій Дніпропетровській області",
        NOW-timedelta(hours=5))])
    rows=scoped_candidates(post,state,NOW)
    assert len([r for r in rows if r[3]])==1



def test_partial_pokrovskyi_district_notice_sent_only_with_private_scope():
    from alerts import region_candidates
    private_scope={**HOUSE_SCOPE,"district":"Покровський район","quarter":"129 квартал"}
    page=html_page([fake_post(11,
        "Кривий Ріг: частково знеструмлено Покровський район")])
    narrow=region_candidates(page,{"official:last_id":10},NOW,scope=private_scope)
    notes=[r[3] for r in narrow if r[3]]
    assert len(notes)==1
    assert "часткове знеструмлення" in notes[0]
    assert "Невідомо, чи зачепило відключення конкретний будинок" in notes[0]
    broad=region_candidates(page,{"official:last_id":10},NOW,scope=HOUSE_SCOPE)
    assert not [r for r in broad if r[3]]


def test_partial_quarter_notice_sent_only_for_matching_private_quarter():
    from alerts import region_candidates
    private_scope={**HOUSE_SCOPE,"district":"Покровський район","quarter":"129 квартал"}
    page=html_page([fake_post(11,
        "Кривий Ріг: частково знеструмлено 129 квартал")])
    matches=region_candidates(page,{"official:last_id":10},NOW,scope=private_scope)
    assert len([r for r in matches if r[3]])==1
    miss=region_candidates(page,{"official:last_id":10},NOW,
        scope={**private_scope,"quarter":"130 квартал"})
    assert not [r for r in miss if r[3]]


def test_partial_area_outage_report_detected_as_distinct_event():
    event=regional_event(fake_post(13,
        "Дніпропетровщина: частково знеструмлено область"))
    assert event and event["kind"]=="area" and event["status"]=="active"


def test_different_city_inside_the_same_oblast_is_silent():
    page=html_page([fake_post(14,
        "Дніпропетровщина: екстрені відключення лише у місті Нікополь")])
    assert not [r for r in scoped_candidates(page,{"official:last_id":10},NOW) if r[3]]
