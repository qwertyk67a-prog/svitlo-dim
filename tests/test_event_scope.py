"""Geography: never notify this building for uncertain or unrelated outages."""
import json
from event_scope import covers_house, house_scope_from_secret

DEFAULT = {
    "city": "м. Кривий Ріг",
    "street": "вул. Умовна",
    "house": "12Г",
}
EXTENDED = {**DEFAULT, "district": "Покровський район", "quarter": "129 квартал"}


def test_generic_oblast_or_city_headlines_do_not_prove_house_coverage():
    headlines = (
        "Дніпропетровщина: запроваджено екстрені відключення",
        "Кривий Ріг: запроваджені екстрені відключення",
        "Дніпропетровщина: графіки аварійних відключень",
        "Дніпропетровщина: частина області без світла",
        "Кривий Ріг: світла немає на деяких вулицях",
        "Україна: введено екстрені відключення в деяких регіонах",
    )
    assert all(not covers_house(v, DEFAULT) for v in headlines)


def test_entire_nation_region_city_are_covered():
    positive = (
        "Укренерго: екстрені відключення діють по всій Україні",
        "Україна: обмеження запроваджені в усіх областях України",
        "Дніпропетровщина: введені екстрені відключення по всій Дніпропетровській області",
        "Дніпропетровщина: екстрені відключення на всій Дніпропетровщині",
        "Кривий Ріг: екстрені відключення діють у всьому місті Кривому Розі",
        "Кривий Ріг: екстрені відключення охопили весь Кривий Ріг",
        "Кривий Ріг: скасовано екстрені відключення у всьому Кривому Розі",
    )
    assert all(covers_house(v, DEFAULT) for v in positive)


def test_explicit_entire_district_or_quarter_requires_private_metadata():
    district = "Кривий Ріг: відключено весь Покровський район"
    quarter = "Кривий Ріг: знеструмлений весь 129 квартал"
    assert covers_house(district, EXTENDED)
    assert covers_house(quarter, EXTENDED)
    assert not covers_house(district, DEFAULT)
    assert not covers_house(quarter, DEFAULT)
    assert not covers_house("Інше місто: весь Покровський район", EXTENDED)
    assert not covers_house("весь 129 квартал", EXTENDED)


def test_entire_other_district_or_quarter_not_covered():
    assert not covers_house("Кривий Ріг: весь Центральний район", EXTENDED)
    assert not covers_house("Кривий Ріг: весь 130 квартал", EXTENDED)


def test_exact_house_ref_qualifies_but_neighbor_does_not():
    assert covers_house("Кривий Ріг: аварія, вул. Умовна, буд. 12Г", DEFAULT)
    assert covers_house("Кривий Ріг: аварія, вул. Умовна, 12/Г", DEFAULT)
    assert not covers_house("Кривий Ріг: аварія, вул. Умовна, 12Д", DEFAULT)
    assert not covers_house("Кривий Ріг: аварія, вул. Умовна, 12", DEFAULT)
    assert not covers_house("Кривий Ріг: аварія, вул. Інша, буд. 12Г", DEFAULT)
    assert not covers_house("Кривий Ріг: аварія, вул. Умовна", DEFAULT)


def test_negative_or_exception_text_prevents_false_positive():
    assert not covers_house(
        "В усій Дніпропетровській області, крім Кривого Рогу, введено екстрені відключення",
        EXTENDED,
    )
    assert not covers_house(
        "У всьому Кривому Розі, за винятком Покровського району, діють обмеження",
        EXTENDED,
    )
    assert not covers_house(
        "ДТЕК: екстрені відключення не стосуються цього будинку: вул. Умовна 12Г",
        EXTENDED,
    )


def test_private_extra_metadata_optional_and_never_in_public_source():
    minimal=house_scope_from_secret(json.dumps(DEFAULT,ensure_ascii=False))
    assert minimal["district"] == "" and minimal["quarter"] == ""
    expanded=house_scope_from_secret(json.dumps(EXTENDED,ensure_ascii=False))
    assert expanded["district"] == "Покровський район"
    assert expanded["quarter"] == "129 квартал"
    assert not covers_house("вся Україна", None)


def test_no_scope_no_alert():
    assert not covers_house("По всій Україні застосовано екстрені відключення", None)
    assert not covers_house("По всій Україні застосовано екстрені відключення", {})
