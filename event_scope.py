"""Conservative geographical relevance for the privately configured house.

A PARTIAL outage of a territory containing the building is relevant.
It is NOT proof the building itself has lost electricity.
Never commit the private address or persist it in bot_state.json.
"""
import json
import re


def _clean(value):
    return re.sub(r"\s+", " ", str(value).casefold().replace("’", "'")).strip()


def house_scope_from_secret(secret):
    """Only three fields are sent to DTEK; optional geography stays private."""
    from alerts import address_fields
    raw = json.loads(secret)
    city, street, house = address_fields(secret)
    result = {"city": city, "street": street, "house": house}
    for optional in ("district", "quarter"):
        value = raw.get(optional, "")
        if not isinstance(value, str) or len(value) > 120:
            raise ValueError(f"Некоректне додаткове поле {optional}")
        result[optional] = value.strip()
    return result


CITY_NAMES = ("кривий ріг", "кривого рогу", "кривому розі", "кривому рогу")
REGION_NAMES = ("дніпропетровщина", "дніпропетровщині", "дніпропетровщини")

EXCEPTION = re.compile(
    r"\b(?:крім|окрім|за\s+винятком|за\s+виключенням|"
    r"не\s+стос\w*|не\s+поширю\w*|не\s+охоплю\w*)\b"
)

DISTRICT_NAME = re.compile(r"\b([а-яіїєґ'-]{4,})\s+район\w*\b")
QUARTER_NAME = re.compile(r"\b(\d{1,4})\s*[- ]?\s*квартал\w*\b")
OTHER_CITY = re.compile(
    r"\b(?:в|у)\s+(?:місті\s+)?(?:дніпрі|нікополі|павлограді|"
    r"кам'янському|самарі|жовтих водах)\b|"
    r"\b(?:місто|місті|м\.)\s*(?:дніпро|нікополь|павлоград|"
    r"кам'янське|самар|жовті води)\b"
)
OTHER_REGION = re.compile(
    r"\b(?:харківськ\w*|київськ\w*|одеськ\w*|львівськ\w*|"
    r"полтавськ\w*|запорізьк\w*|чернігівськ\w*)\s+област\w*\b"
)

def _known_city(scope):
    city = _clean(scope.get("city", ""))
    city = re.sub(r"^(?:м\.?|місто)\s+", "", city)
    return city in CITY_NAMES


def _exact_house(text, scope):
    street = _clean(scope["street"])
    street = re.sub(r"^(?:вул\.?|вулиця|просп\.?|проспект)\s+", "", street)
    street = re.sub(r"\s+", r"\\s+", re.escape(street))
    house = re.sub(r"[\s/\\.\-]", "", _clean(scope["house"]))
    match = re.fullmatch(r"(\d+)([а-яіїєґa-z]?)", house)
    if not match or not street:
        return False
    number, letter = match.groups()
    letter = {"a": "[аa]", "b": "[вb]", "i": "[іi]"}.get(letter, re.escape(letter))
    number_regex = rf"(?<!\d){re.escape(number)}[\s/\\.\-]*{letter}(?![\w])"
    return bool(re.search(
        rf"\b{street}\b[\s,;:№#.\-/\\]*(?:буд(?:инок)?\.?\s*№?\s*)?"
        rf"[\s,;:№#.\-/\\]{{0,18}}{number_regex}", text
    ))


def _district_matches(candidate, configured):
    """Accept inflected forms, not a different district in the same city."""
    expected = _clean(configured)
    match = DISTRICT_NAME.search(expected)
    if not match:
        return False
    word = match.group(1)
    # Покровський, Покровському and Покровського share "покров".
    stem = re.sub(r"(?:ськ\w*|цк\w*|цьк\w*|ск\w*|ого|ому|ий)$", "", word)
    return len(stem) >= 4 and candidate.startswith(stem)


def _quarter_matches(number, configured):
    match = QUARTER_NAME.search(_clean(configured))
    return bool(match and match.group(1) == number)


def _partial(text):
    return bool(re.search(
        r"\b(?:частков\w*|частин\w*|деяк\w*|окрем\w*|"
        r"кільк\w*|вибірков\w*|подекуди)\b", text
    ))


def coverage_for_house(announcement, scope):
    """Return coverage level when the declared affected AREA contains the house.

    This deliberately avoids claiming the house lost voltage. When an
    announcement specifies a smaller place, it takes priority over its
    broad regional headline (e.g. "Dnipro region: only Nikopol").
    """
    if (not isinstance(scope, dict) or
            any(not isinstance(scope.get(k), str) or not scope[k].strip()
                for k in ("city", "street", "house"))):
        return None
    text = _clean(announcement)
    if not text or EXCEPTION.search(text):
        return None

    if _exact_house(text, scope):
        return {"level": "house", "partial": False, "label": "будинок"}

    # If a report specifies ANOTHER address, the wider city header does not
    # make that address relevant. A street-only notice is also insufficient.
    if re.search(r"\b(?:вул\.?|вулиця|просп\.?|проспект|будинок|буд\.)\s+", text):
        return None

    # Explicit narrower locality beats the broader regional headline.
    districts = DISTRICT_NAME.findall(text)
    if districts:
        if not any(city in text for city in CITY_NAMES):
            return None  # Same-named districts can exist in different cities.
        if any(_district_matches(word, scope.get("district", ""))
               for word in districts):
            return {
                "level": "district", "partial": True,
                "label": scope["district"].strip(),
            }
        return None

    quarters = QUARTER_NAME.findall(text)
    if quarters:
        if not any(city in text for city in CITY_NAMES):
            return None
        if any(_quarter_matches(number, scope.get("quarter", ""))
               for number in quarters):
            return {
                "level": "quarter", "partial": True,
                "label": scope["quarter"].strip(),
            }
        return None

    if OTHER_CITY.search(text) and not any(name in text for name in CITY_NAMES):
        return None
    if OTHER_REGION.search(text) and not (
        "дніпропетровськ" in text or any(name in text for name in REGION_NAMES)
    ):
        return None

    if not _known_city(scope):
        return None

    if any(name in text for name in CITY_NAMES):
        return {"level": "city", "partial": _partial(text),
                "label": "Кривий Ріг"}

    if "дніпропетровськ" in text or any(
        name in text for name in REGION_NAMES
    ):
        return {"level": "region", "partial": _partial(text),
                "label": "Дніпропетровська область"}

    if re.search(
        r"\b(?:україн\w*|усіх\s+област\w*|всіх\s+област\w*)\b",
        text,
    ):
        return {"level": "country", "partial": _partial(text),
                "label": "Україна"}

    return None


def covers_house(announcement, scope):
    """Compatibility boolean for pure geographical filtering."""
    return coverage_for_house(announcement, scope) is not None
