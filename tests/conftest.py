"""Synthetic provider settings for tests. No real property or operator details."""
import json
import pytest

TEST_CONFIG = {
    "adapter": "ajax_v1",
    "operator": "ПрикладЕнерго",
    "state_key": "synthetic-repository-checkpoint-key-not-a-secret",
    "site_url": "https://grid.example.invalid/schedule",
    "ajax_url": "https://grid.example.invalid/ajax",
    "telegram_feed": "https://t.me/s/sample_energy",
    "city_label": "Зелений Бір",
    "city_forms": ["зелений бір", "зеленого бору", "зеленому борі"],
    "region_label": "Прикладна область",
    "region_forms": [
        "прикладщина", "прикладщині", "прикладщини",
        "прикладна область", "прикладній області", "прикладної області",
        "прикладській області", "прикладської області",
    ],
    "other_places": [
        "іншоград", "іншограда", "іншограді",
        "другоград", "другограда", "другограді",
        "південний гай", "південного гаю",
        "каменяр", "каменяра", "річне", "малополянськ",
    ],
    "other_regions": ["інша область", "сусідня область", "харківській області"],
    "headline_prefixes": ["прикладенерго"],
    "address": {
        "city": "м. Зелений Бір",
        "street": "вул. Умовна",
        "house": "12Г",
        "district": "Зразковий район",
        "quarter": "987 квартал",
    },
}


@pytest.fixture(autouse=True)
def private_synthetic_provider(monkeypatch):
    """Never require GitHub Secrets while running pure unit tests."""
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(
        TEST_CONFIG, ensure_ascii=False,
    ))
