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
    **DEFAULT, "district": "Зразковий район", "quarter": "987 квартал",
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
        "Кривий Ріг: відновлено електропостачання після знеструмлення у Зразковому районі",
        "Кривий Ріг: частково знеструмлено у 987 кварталі",
    )
    assert all(covers_house(text,FULL) for text in positive)


def test_partial_district_and_quarter_require_private_metadata():
    district=coverage_for_house(
        "Кривий Ріг: частково знеструмлений Зразковий район",FULL)
    assert district and district["level"]=="district"
    assert district["partial"] is True
    quarter=coverage_for_house(
        "Кривий Ріг: частково знеструмлено 987 квартал",FULL)
    assert quarter and quarter["level"]=="quarter"
    assert quarter["partial"] is True
    assert not covers_house(
        "Кривий Ріг: частково знеструмлений Зразковий район",DEFAULT)
    assert not covers_house(
        "Кривий Ріг: частково знеструмлено 987 квартал",DEFAULT)


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
        "Кривий Ріг: аварія, вул. Умовна, 12-Г",
    )
    no = (
        "Кривий Ріг: аварія, вул. Умовна, 12/Г",
        "Кривий Ріг: аварія, вул. Умовна, 12Д",
        "Кривий Ріг: аварія, вул. Умовна, 12",
        "Кривий Ріг: аварія, вул. Інша, буд. 12Г",
    )
    assert all(coverage_for_house(t,FULL)["level"]=="house" for t in yes)
    assert all(not covers_house(t,FULL) for t in no)


def test_exception_to_area_makes_coverage_uncertain_so_silent():
    for t in (
        "В усій Дніпропетровській області, крім Кривого Рогу, введено відключення",
        "У всьому Кривому Розі, за винятком Зразкового району, діють обмеження",
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
    assert extended["district"]=="Зразковий район"
    assert extended["quarter"]=="987 квартал"



def test_some_regions_of_ukraine_do_not_prove_this_region_is_affected():
    examples=(
        "Україна: екстрені відключення у деяких регіонах",
        "Україна: обмеження запроваджені в кількох областях",
        "Укренерго: в окремих областях України запроваджені обмеження",
    )
    assert all(not covers_house(text,FULL) for text in examples)
    assert covers_house("Укренерго: обмеження по всій Україні",FULL)



def test_matching_street_partial_outage_is_relevant_without_house_number():
    report="Кривий Ріг: частково знеструмлена вул. Умовна"
    got=coverage_for_house(report,FULL)
    assert got and got["level"]=="street" and got["partial"]


def test_specific_other_house_on_our_street_is_not_relevant():
    assert not covers_house(
        "Кривий Ріг: аварійне відключення, вул. Умовна, буд. 12Д", FULL
    )
    assert not covers_house(
        "Кривий Ріг: вул. Умовна, 12Д без світла", FULL
    )
    assert not covers_house(
        "Кривий Ріг: частково знеструмлена вул. Інша",FULL
    )



def test_explicit_exclusion_of_unrelated_city_does_not_silence_our_oblast():
    assert covers_house(
        "Дніпропетровщина: відключення по всій Дніпропетровській області, "
        "крім Нікополя", FULL
    )
    assert not covers_house(
        "Дніпропетровщина: відключення по всій Дніпропетровській області, "
        "крім Кривого Рогу", FULL
    )


def test_excluding_other_district_keeps_our_district_notification():
    assert covers_house(
        "Кривий Ріг: екстрені відключення по всьому місту, "
        "крім Центрального району",FULL
    )
    assert not covers_house(
        "Кривий Ріг: екстрені відключення по всьому місту, "
        "крім Зразкового району",FULL
    )


def test_unrecognized_exception_remains_silent_instead_of_guessing():
    assert not covers_house(
        "Дніпропетровщина: запроваджені обмеження крім деяких районів", FULL
    )


def test_region_headline_with_only_other_towns_is_not_house_notice():
    examples = (
        "Дніпропетровщина: екстрені відключення лише у місті Марганець",
        "Дніпропетровщина: екстрені відключення в окремих громадах",
        "Дніпропетровщина: екстрені відключення у деяких містах",
    )
    assert all(not covers_house(item, FULL) for item in examples)


def test_named_other_towns_in_plural_is_not_whole_oblast():
    assert not covers_house(
        "Дніпропетровщина: запроваджено екстрені відключення "
        "у містах Нікополь та Павлоград", FULL
    )


def test_named_other_cities_without_whole_region_are_silent():
    examples = [
        "Дніпропетровщина: після обстрілу знеструмлено Дніпро та Павлоград",
        "Дніпропетровщина: знеструмлено Синельникове",
        "Дніпропетровщина: без світла мешканці Нікополя і Кам'янського",
    ]
    assert all(not covers_house(x, FULL) for x in examples)
    assert covers_house(
        "Дніпропетровщина: екстрені відключення по всій області, "
        "включно з Дніпром і Павлоградом", FULL
    )
