"""Conservative geographical coverage checks for official DTEK announcements.

Only an explicit blanket zone or an exact private house reference qualifies.
A regional HEADLINE by itself never proves an outage affects the building.
Do not store the private address in source code, logs or notification state.
"""
import json
import re

EXCLUSION = re.compile(
    r"\b(?:крім|окрім|за\s+винятком|не\s+стосує\w*|не\s+поширює\w*|"
    r"не\s+охоплює\w*|за\s+виключенням)\b",
    re.I,
)

ALL_UKRAINE = (
    r"\b(?:по\s+)?(?:всій|усій|цілій)\s+україн\w*\b",
    r"\b(?:по\s+)?(?:всій|усій|цілій)\s+територі\w*\s+україн\w*\b",
    r"\b(?:всіх|усіх)\s+област\w*\s+україн\w*\b",
    r"\b(?:вся|уся|ціла)\s+україн\w*\b",
    r"\bукраїн\w*\s+повністю\b",
)

ALL_DNIPRO_REGION = (
    r"\b(?:по\s+)?(?:всій|усій|цілій)\s+дніпропетровськ\w*\s+област\w*\b",
    r"\b(?:по\s+)?(?:всій|усій|цілій)\s+дніпропетровщин\w*\b",
    r"\b(?:вся|уся|ціла)\s+дніпропетровськ\w*\s+област\w*\b",
    r"\bдніпропетровськ\w*\s+област\w*\s+повністю\b",
    r"\bдніпропетровщин\w*\s+повністю\b",
    r"\b(?:всіх|усіх)\s+(?:споживач\w*|адрес\w*|будинк\w*)\s+"
    r"дніпропетровськ\w*\s+област\w*\b",
)

ALL_KRYVYI_RIH = (
    r"\b(?:весь|увесь|цілий)\s+(?:місто\s+)?крив(?:ий|ого|ому)\s+(?:ріг|рогу|розі)\b",
    r"\b(?:всьому|усьому|цілому)\s+(?:місті\s+)?крив(?:ому|ий)\s+(?:розі|рогу|ріг)\b",
    r"\bкрив(?:ий|ого|ому)\s+(?:ріг|рогу|розі)\s+повністю\b",
    r"\b(?:всіх|усіх)\s+(?:споживач\w*|адрес\w*|будинк\w*)\s+"
    r"(?:міста\s+)?кривого\s+рогу\b",
)

CITY_ALIASES = (
    "кривий ріг", "кривого рогу", "кривому розі", "кривому рогу",
)


def _clean(text):
    return re.sub(r"\s+", " ", str(text).casefold().replace("’", "'")).strip()


def house_scope_from_secret(secret):
    """Use private address solely during runtime, never write to public state."""
    raw = json.loads(secret)
    from alerts import address_fields
    city, street, house = address_fields(secret)
    scope = {"city": city, "street": street, "house": house}
    for option in ("district", "quarter"):
        value = raw.get(option, "")
        if not isinstance(value, str) or len(value) > 120:
            raise ValueError(f"Некоректне додаткове поле {option}")
        scope[option] = value.strip()
    return scope


def _known_city(scope):
    city = _clean(scope.get("city", ""))
    city = re.sub(r"^(?:м\.?|місто)\s+", "", city)
    return city in CITY_ALIASES


def _exact_house_reference(text, scope):
    """Exact street+house together, not a street mentioned without house."""
    street = _clean(scope.get("street", ""))
    street = re.sub(r"^(?:вул\.?|вулиця|просп\.?|проспект)\s+", "", street)
    street = re.sub(r"\s+", r"\\s+", re.escape(street))
    house = re.sub(r"[\s/\\.\-]", "", _clean(scope.get("house", "")))
    m = re.fullmatch(r"(\d+)([а-яa-z]?)", house)
    if not m or not street:
        return False
    number, suffix = m.groups()
    suffix = {"a": "[aа]", "b": "[bв]", "i": "[iі]"}.get(
        suffix, re.escape(suffix)
    )
    house_pattern = (
        r"(?<!\d)" + re.escape(number) +
        r"[\s/\\.\-]*" + suffix + r"(?![\w])"
    )
    # Require the house number near the correct street; a street-only
    # outage or a different house number is not sufficient.
    return bool(re.search(
        rf"\b{street}\b[\s,;:№#.\-/\\]*(?:буд(?:инок)?\.?\s*№?\s*)?"
        rf"[\s,;:№#.\-/\\]{{0,18}}{house_pattern}",
        text,
    ))


def _area_is_whole(text, stem, noun):
    if not stem:
        return False
    part = re.escape(stem) + r"\w*"
    return bool(re.search(
        rf"\b(?:весь|увесь|цілий)\s+{part}\s+{noun}\w*\b|"
        rf"\b(?:всьому|усьому|цілому)\s+{part}\s+{noun}\w*\b|"
        rf"\b{part}\s+{noun}\w*\s+повністю\b",
        text,
    ))


def covers_house(announcement, scope):
    """True only for an unambiguous ALL-territory or exact-house notice.

    Deliberately refuse ambiguous regional notices, exclusions and prose
    about unrelated outages. Fail closed rather than generate noise.
    """
    if (not isinstance(scope, dict) or
            any(not isinstance(scope.get(key), str) or not scope[key].strip()
                for key in ("city", "street", "house"))):
        return False
    text = _clean(announcement)
    if not text or EXCLUSION.search(text):
        return False

    if any(re.search(p, text) for p in ALL_UKRAINE):
        return True

    if not _known_city(scope):
        # This bot is configured for DTEK dnem and its known city.
        # A different city requires an explicit house match.
        return _exact_house_reference(text, scope)

    if any(re.search(p, text) for p in ALL_DNIPRO_REGION + ALL_KRYVYI_RIH):
        return True

    if _exact_house_reference(text, scope):
        return True

    # An entire district/quarter qualifies only with extra details in the
    # private address secret AND an explicit city mention in the report.
    if not any(name in text for name in CITY_ALIASES):
        return False

    district = _clean(scope.get("district", ""))
    m = re.search(r"([а-яіїєґ]{5,})\w*\s+район\w*", district)
    if m:
        stem = re.sub(r"(?:ськ|цк|цьк|ск|ий|ого|ому)+$", "", m.group(1))
        if _area_is_whole(text, stem, "район"):
            return True
    quarter = _clean(scope.get("quarter", ""))
    qm = re.search(r"(?<!\d)(\d{1,4})\s*[- ]?\s*квартал", quarter)
    if qm:
        q = re.escape(qm.group(1))
        if re.search(
            rf"\b(?:весь|увесь|цілий)\s+{q}\s*[- ]?\s*квартал\w*\b|"
            rf"\b{q}\s*[- ]?\s*квартал\w*\s+повністю\b",
            text,
        ):
            return True
    return False
