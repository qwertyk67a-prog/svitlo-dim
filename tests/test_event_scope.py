"""Territory includes our house, even when the outage is only partial.

Do NOT claim the actual building is off without an explicit address report.
"""
import json
from event_scope import (
    covers_house, coverage_for_house, house_scope_from_secret,
)

DEFAULT = {
    "city": "м. Кривий Ріг", "street": "вул. Умовна", "house": "12Г",
}
FULL = {
    **DEFAULT, "district": "Покровський район", "quarter": "129 квартал",
}


def test_region_named_as_affected_is_relevant_even_if_only_partially():
    examples = (
        ("Дніпропетровщина: запроваджено екстрені відключення",
         "region", False),
        ("Дніпропетровщина: частково знеструмлена область",
         "region", True),
        ("Дніпропетровщина: графіки аварійних відключень",
         "region", False),
        ("Кривий Ріг: частково знеструмлено місто",
         "city", True),
        ("Кривий Ріг: запроваджені екстрені відключення",
         "city", False),
        ("Україна: часткові обмеження по всій Україні",
         "country", True),
    )
    for event, level, partial in examples:
        got=coverage_for_house(event,FULL)
        assert got is not None, event
        assert got["level"]==level, event
        assert got["partial"] is partial, event


def test_entire_territory_and_restoration_are_relevant():
    positive = (
        "Укренерго: екстрені відключення діють по всій Україні",
        "Україна: обмеження запроваджені в усіх областях України",
        "Дніпропетровщина: екстрені відключення по всій Дніпропетровській області",
        "Кривий Ріг: екстрені відключення діють у всьому місті Кривому Розі",
        "Кривий Ріг: відновлено електропостачання після знеструмлення у Покровському районі",
        "Кривий Ріг: частково знеструмлено у 129 кварталі",
    )
    assert all(covers_house(text,FULL) for text in positive)


def test_partial_district_and_quarter_require_private_metadata():
    district=coverage_for_house(
        "Кривий Ріг: частково знеструмлений Покровський район",FULL)
    assert district and district["level"]=="district"
    assert district["partial"] is True
    quarter=coverage_for_house(
        "Кривий Ріг: частково знеструмлено 129 квартал",FULL)
    assert quarter and quarter["level"]=="quarter"
    assert quarter["partial"] is True
    assert not covers_house(
        "Кривий Ріг: частково знеструмлений Покровський район",DEFAULT)
    assert not covers_house(
        "Кривий Ріг: частково знеструмлено 129 квартал",DEFAULT)


def test_other_quarter_district_and_city_are_not_our_house():
    rejected = (
        "Кривий Ріг: знеструмлено частину Центрального району",
        "Кривий Ріг: частково знеструмлений Довгинцівський район",
        "Кривий Ріг: частково знеструмлений 130 квартал",
        "Дніпропетровщина: екстрені відключення у місті Нікополь",
        "Дніпропетровщина: екстрені відключення тільки у Дніпрі",
        "Україна: аварійні відключення тільки у Харківській області",
    )
    assert all(not covers_house(t,FULL) for t in rejected)


def test_exact_house_reference_matches_but_neighbor_does_not():
    yes = (
        "Кривий Ріг: аварія, вул. Умовна, буд. 12Г",
        "Кривий Ріг: аварія, вул. Умовна, 12/Г",
    )
    no = (
        "Кривий Ріг: аварія, вул. Умовна, 12Д",
        "Кривий Ріг: аварія, вул. Умовна, 12",
        "Кривий Ріг: аварія, вул. Інша, буд. 12Г",
    )
    assert all(coverage_for_house(t,FULL)["level"]=="house" for t in yes)
    assert all(not covers_house(t,FULL) for t in no)


def test_exception_to_area_makes_coverage_uncertain_so_silent():
    for t in (
        "В усій Дніпропетровській області, крім Кривого Рогу, введено відключення",
        "У всьому Кривому Розі, за винятком Покровського району, діють обмеження",
        "ДТЕК: екстрені відключення не стосуються цього будинку: вул. Умовна 12Г",
    ):
        assert not covers_house(t,FULL)


def test_no_address_no_coverage():
    for bad in (None,{},{"city":"м. Кривий Ріг"}):
        assert not covers_house("Дніпропетровщина: частково знеструмлено",bad)


def test_private_fields_are_optional_and_stay_runtime_only():
    basic=house_scope_from_secret(json.dumps(DEFAULT,ensure_ascii=False))
    assert basic["district"]=="" and basic["quarter"]==""
    extended=house_scope_from_secret(json.dumps(FULL,ensure_ascii=False))
    assert extended["district"]=="Покровський район"
    assert extended["quarter"]=="129 квартал"



def test_some_regions_of_ukraine_do_not_prove_this_region_is_affected():
    examples=(
        "Україна: екстрені відключення у деяких регіонах",
        "Україна: обмеження запроваджені в кількох областях",
        "Укренерго: в окремих областях України запроваджені обмеження",
    )
    assert all(not covers_house(text,FULL) for text in examples)
    assert covers_house("Укренерго: обмеження по всій Україні",FULL)
