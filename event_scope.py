"""Conservative territorial relevance for a privately configured address.

Geographical names are loaded from POWER_CONFIG_JSON, not this module.
A territorial notice never proves electricity is absent at a specific home.
"""
import json
import re

from power_config import provider_config


def _clean(value):
    return re.sub(r"\s+", " ", str(value).casefold().replace("’", "'")).strip()


def _forms(scope, key):
    configured = scope.get(key)
    if isinstance(configured, (tuple, list)):
        return tuple(_clean(v) for v in configured if v)
    value = provider_config(required=False).get(key, ())
    return tuple(_clean(v) for v in value if v)


def _contains(text, words):
    """Match complete locality words, not stems inside a different place name."""
    return any(
        re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text)
        for word in words if word
    )


def _place_config(scope):
    config = provider_config(required=False)
    return {
        "city_forms": _forms(scope, "city_forms"),
        "region_forms": _forms(scope, "region_forms"),
        "other_places": _forms(scope, "other_places"),
        "other_regions": _forms(scope, "other_regions"),
        "city_label": scope.get("city_label") or config.get("city_label", ""),
        "region_label": scope.get("region_label") or config.get("region_label", ""),
    }


def house_scope_from_secret(secret):
    from alerts import address_fields
    raw = json.loads(secret)
    city, street, house = address_fields(secret)
    config = provider_config()
    scope = {"city": city, "street": street, "house": house}
    for key in ("district", "quarter"):
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > 120:
            raise ValueError("Некоректні географічні дані")
        scope[key] = value.strip()
    for key in ("city_forms", "region_forms", "other_places", "other_regions"):
        scope[key] = config[key]
    scope["city_label"] = config["city_label"]
    scope["region_label"] = config["region_label"]
    return scope


EXCEPTION = re.compile(
    r"\b(?:крім|окрім|за\s+винятком|за\s+виключенням|"
    r"не\s+стос\w*|не\s+поширю\w*|не\s+охоплю\w*)\b"
)
DISTRICT_NAME = re.compile(r"\b([а-яіїєґ'-]{4,})\s+район\w*\b")
QUARTER_NAME = re.compile(r"\b(\d{1,4})\s*[- ]?\s*квартал\w*\b")


def _known_city(scope):
    city = _clean(scope.get("city", ""))
    city = re.sub(r"^(?:м\.?|місто)\s+", "", city)
    territory = _place_config(scope)
    expected = _clean(territory["city_label"])
    return bool(expected and (city == expected or city in territory["city_forms"]))


def _exact_house(text, scope):
    street = _clean(scope["street"])
    street = re.sub(r"^(?:вул\.?|вулиця|просп\.?|проспект)\s+", "", street)
    street = re.escape(street).replace(r"\ ", r"\s+")
    from alerts import normalized_house
    house = normalized_house(scope["house"])
    match = re.fullmatch(r"(\d+)([а-яіїєґ]?)(?:/(\d+[а-яіїєґ]?))?", house)
    if not match or not street:
        return False
    number, letter, subnumber = match.groups()
    letter_regex = {"а": "[аa]", "в": "[вb]", "і": "[іi]"}.get(
        letter, re.escape(letter)
    )
    number_regex = rf"(?<![\d/]){re.escape(number)}"
    if letter:
        number_regex += rf"[\s\-]*{letter_regex}"
    if subnumber:
        number_regex += rf"\s*/\s*{re.escape(subnumber)}"
    number_regex += r"(?![\w/]|\s*/\s*[\w]|[.\-][\w])"
    return bool(re.search(
        rf"\b{street}\b[\s,;:№#.\-/\\]*(?:буд(?:инок)?\.?\s*№?\s*)?"
        rf"[\s,;:№#.\-/\\]{{0,18}}{number_regex}", text
    ))


def _matching_street_without_other_house(text, scope):
    configured = _clean(scope.get("street", ""))
    configured = re.sub(
        r"^(?:вул\.?|вулиця|просп\.?|проспект)\s+", "", configured
    )
    if not configured:
        return False
    escaped = re.escape(configured).replace(r"\ ", r"\s+")
    match = re.search(
        rf"\b(?:вул\.?|вулиця|просп\.?|проспект)\s+{escaped}\b", text
    )
    if not match:
        return False
    next_part = text[match.end():match.end() + 70]
    return not bool(re.search(
        r"^\s*[,;:№#\-/ ]*\d+[а-яa-z]?\b|"
        r"\b(?:будинок|буд\.?|будинки|№)\s*[\d№]", next_part
    ))


def _district_matches(candidate, configured):
    expected = _clean(configured)
    match = DISTRICT_NAME.search(expected)
    if not match:
        return False
    stem = re.sub(
        r"(?:ськ\w*|цк\w*|цьк\w*|ск\w*|ого|ому|ий)$",
        "", match.group(1),
    )
    return len(stem) >= 4 and candidate.startswith(stem)


def _quarter_matches(number, configured):
    match = QUARTER_NAME.search(_clean(configured))
    return bool(match and match.group(1) == number)


def _partial(text):
    return bool(re.search(
        r"\b(?:частков\w*|частин\w*|деяк\w*|окрем\w*|"
        r"кільк\w*|вибірков\w*|подекуди)\b", text
    ))


def _exclusion_might_include_house(text, scope):
    match = EXCEPTION.search(text)
    if not match:
        return False
    affected = text[match.end():]
    territory = _place_config(scope)
    if match.group(0).startswith("не "):
        return True
    if _exact_house(affected, scope):
        return True
    if _contains(affected, territory["city_forms"]):
        return True
    if _contains(affected, territory["region_forms"]):
        return True
    districts = DISTRICT_NAME.findall(affected)
    if districts:
        if any(re.match(
            r"^(?:деяк|окрем|кільк|інш|певн|частин|невідом)\w*", x
        ) for x in districts):
            return True
        configured = scope.get("district", "")
        return not configured or any(
            _district_matches(x, configured) for x in districts
        )
    quarters = QUARTER_NAME.findall(affected)
    if quarters:
        configured = scope.get("quarter", "")
        return not configured or any(
            _quarter_matches(n, configured) for n in quarters
        )
    if _contains(affected, territory["other_places"]):
        return False
    if _contains(affected, territory["other_regions"]):
        return False
    return True  # Unknown exceptions suppress a claim of relevance.


def coverage_for_house(announcement, scope):
    if (not isinstance(scope, dict) or any(
        not isinstance(scope.get(k), str) or not scope[k].strip()
        for k in ("city", "street", "house")
    )):
        return None
    text = _clean(announcement)
    territory = _place_config(scope)
    city_forms = territory["city_forms"]
    region_forms = territory["region_forms"]
    if not city_forms or not region_forms or not _known_city(scope):
        return None  # Private geographical configuration is required.
    if not text or _exclusion_might_include_house(text, scope):
        return None

    exception = EXCEPTION.search(text)
    affected = text[:exception.start()].strip(" ,;:.") if exception else text
    city_named = _contains(affected, city_forms)
    address_place_verified = (
        city_named and not _contains(affected, territory["other_places"])
        and not _contains(affected, territory["other_regions"])
    )
    if _exact_house(affected, scope):
        if not address_place_verified:
            return None
        return {"level": "house", "partial": False, "label": "будинок"}
    if _matching_street_without_other_house(affected, scope):
        if not address_place_verified:
            return None
        return {"level": "street", "partial": _partial(affected),
                "label": "вулиця будинку"}
    if re.search(
        r"\b(?:вул\.?|вулиця|просп\.?|проспект|будинок|буд\.)\s+", affected
    ):
        return None

    region_named = _contains(affected, region_forms)
    districts = DISTRICT_NAME.findall(affected)
    if districts:
        if not city_named:
            return None
        if any(_district_matches(d, scope.get("district", "")) for d in districts):
            return {"level": "district", "partial": True,
                    "label": scope["district"].strip()}
        return None
    quarters = QUARTER_NAME.findall(affected)
    if quarters:
        if not city_named:
            return None
        if any(_quarter_matches(q, scope.get("quarter", "")) for q in quarters):
            return {"level": "quarter", "partial": True,
                    "label": scope["quarter"].strip()}
        return None

    if re.search(
        r"\b(?:у|в|на)\s+(?:містах|громадах|населених\s+пунктах)\b",
        affected,
    ) and not city_named:
        return None
    if re.search(
        r"\b(?:лише|тільки|виключно)\s+(?:у|в|на)\s+"
        r"(?:деяк\w*|окрем\w*|кільк\w*|певн\w*)?\s*"
        r"(?:міст\w*|громад\w*|район\w*|населен\w*\s+пункт\w*)\b",
        affected,
    ):
        return None
    if re.search(
        r"\b(?:у|в|на)\s+(?:деяк\w*|окрем\w*|кільк\w*|певн\w*)\s+"
        r"(?:міст\w*|громад\w*|район\w*|населен\w*\s+пункт\w*)\b",
        affected,
    ):
        return None
    if re.search(r"\b(?:у|в)\s+(?:місті|м\.)\s*[а-яіїєґ]", affected
                 ) and not city_named:
        return None
    if _contains(affected, territory["other_regions"]) and not region_named:
        return None
    if re.search(r"\b(?:лише|тільки|виключно)\s+(?:у|в|на)\b",
                 affected) and not city_named and not region_named:
        return None

    whole_region = bool(re.search(
        r"(?:по|на|в|у)\s+всій\s+(?:територі\w*\s+)?област\w*|"
        r"(?:по|на|в|у)\s+всій\s+(?:територі\w*\s+)?"
        r"[\w\s-]*област\w*",
        affected,
    )) or ("всій " + _clean(territory["region_label"])) in affected
    if _contains(affected, territory["other_places"]) and not city_named and not whole_region:
        return None

    if city_named:
        return {"level": "city", "partial": _partial(affected),
                "label": territory["city_label"]}
    if region_named:
        return {"level": "region", "partial": _partial(affected),
                "label": territory["region_label"]}

    if re.search(r"\b(?:україн\w*|усіх\s+област\w*|всіх\s+област\w*)\b", affected):
        nationwide = bool(re.search(
            r"(?:по|на|у|в)\s+(?:всій|усій|цілій)\s+україн\w*|"
            r"(?:всіх|усіх)\s+област\w*|"
            r"(?:всій|усій)\s+територі\w*\s+україн\w*|"
            r"україн\w*\s+повністю", affected,
        ))
        unspecified = bool(re.search(
            r"(?:деяк\w*|окрем\w*|кільк\w*|певн\w*|частин\w*)\s+"
            r"(?:област\w*|регіон\w*|міст\w*)|"
            r"(?:частин\w*|деяк\w*\s+територі\w*)\s+україн\w*",
            affected,
        ))
        if unspecified and not nationwide:
            return None
        return {"level": "country", "partial": _partial(affected),
                "label": "Україна"}
    return None


def covers_house(announcement, scope):
    return coverage_for_house(announcement, scope) is not None
