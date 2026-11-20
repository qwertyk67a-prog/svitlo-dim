"""Private, validated runtime settings for one electricity provider and one home.

The public repository contains no actual operator endpoints or house address.
Only the selected compatible adapter can be used; never execute arbitrary
modules, URLs or Python code supplied in configuration.
"""
import hashlib
import hmac
import json
import os
import re
from urllib.parse import urlparse


class ConfigurationError(RuntimeError):
    pass


def _https_url(value, field):
    if not isinstance(value, str) or len(value) > 500:
        raise ConfigurationError(f"Invalid {field}")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment):
        raise ConfigurationError(f"Invalid {field}")
    return value.rstrip("/")


def _name(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ConfigurationError(f"Invalid {field}")
    return value.strip()


def _forms(value, field):
    if (not isinstance(value, list) or not value
            or len(value) > 100 or any(
                not isinstance(v, str) or not v.strip() or len(v) > 120
                for v in value
            )):
        raise ConfigurationError(f"Invalid {field}")
    return tuple(dict.fromkeys(v.strip().casefold() for v in value))


def provider_config(*, required=True):
    raw = os.getenv("POWER_CONFIG_JSON", "").strip()
    if not raw:
        if required:
            raise ConfigurationError("Missing POWER_CONFIG_JSON repository secret")
        return {}
    try:
        config = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigurationError("Invalid POWER_CONFIG_JSON structure") from None
    if not isinstance(config, dict):
        raise ConfigurationError("Invalid POWER_CONFIG_JSON object")
    if config.get("adapter") != "ajax_v1":
        raise ConfigurationError(
            "Unsupported provider adapter. A new adapter needs code and tests."
        )
    for key in ("operator", "city_label", "region_label"):
        config[key] = _name(config.get(key), key)
    state_key = config.get("state_key")
    # A valid format is not evidence of randomness. Users must generate this
    # value with a cryptographically secure local generator.
    if (not isinstance(state_key, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", state_key) is None
            or len(set(state_key.casefold())) < 10):
        raise ConfigurationError(
            "state_key must be 64 random hexadecimal characters "
            "(generate with Python secrets.token_hex(32))"
        )
    config["state_key"] = state_key
    catalog = config.get("queue_catalog")
    if (not isinstance(catalog, list) or not 1 <= len(catalog) <= 128
            or any(not isinstance(q, str) or
                   not re.fullmatch(r"[\w .:-]{1,48}", q, flags=re.UNICODE)
                   for q in catalog)):
        raise ConfigurationError("queue_catalog must contain 1-128 safe queue IDs")
    if len(catalog) != len(set(catalog)):
        raise ConfigurationError("queue_catalog contains duplicates")
    config["queue_catalog"] = tuple(catalog)
    prefix = config.get("queue_display_prefix", "")
    if not isinstance(prefix, str) or len(prefix) > 24:
        raise ConfigurationError("Invalid queue_display_prefix")
    config["queue_display_prefix"] = prefix
    for key in ("site_url", "ajax_url", "telegram_feed"):
        config[key] = _https_url(config.get(key), key)
    host = urlparse(config["site_url"]).hostname
    if urlparse(config["ajax_url"]).hostname != host:
        raise ConfigurationError("site_url and ajax_url hosts must match")
    feed = urlparse(config["telegram_feed"])
    if feed.hostname not in ("t.me", "telegram.me"):
        raise ConfigurationError("telegram_feed must be a Telegram channel")
    slug = re.fullmatch(r"/s/([A-Za-z0-9_]{5,32})", feed.path)
    if not slug:
        raise ConfigurationError("telegram_feed must use public /s/channel format")
    config["channel_slug"] = slug.group(1)
    for key in ("city_forms", "region_forms"):
        config[key] = _forms(config.get(key), key)
    for key in ("other_places", "other_regions", "headline_prefixes"):
        if key not in config:
            config[key] = []
        if not isinstance(config[key], list):
            raise ConfigurationError(f"Invalid {key}")
        config[key] = tuple(
            str(x).strip().casefold()
            for x in config[key]
            if isinstance(x, str) and x.strip()
        )
    address = config.get("address")
    if not isinstance(address, dict):
        raise ConfigurationError("Missing address in POWER_CONFIG_JSON")
    for key in ("city", "street", "house"):
        address[key] = _name(address.get(key), f"address.{key}")
    for key in ("district", "quarter"):
        item = address.get(key) or ""
        if not isinstance(item, str) or len(item) > 120:
            raise ConfigurationError(f"Invalid address.{key}")
        address[key] = item.strip()
    config["address"] = address
    return config


def operator_label():
    return provider_config(required=False).get("operator", "Оператор електромережі")


def official_site():
    return provider_config(required=False).get("site_url", "")


def source_tag():
    return "Джерело: " + operator_label()


def address_json():
    config = provider_config()
    return json.dumps(config["address"], ensure_ascii=False)


def public_territory(config=None):
    config = config or provider_config()
    return {
        "city_label": config["city_label"],
        "region_label": config["region_label"],
        "city_forms": config["city_forms"],
        "region_forms": config["region_forms"],
        "other_places": config["other_places"],
        "other_regions": config["other_regions"],
        "headline_prefixes": config["headline_prefixes"],
    }


def checkpoint_namespace(config, bot_token="", chat_id=""):
    """Stable per-installation identity, unaffected by JSON formatting.

    A real destination change requires a fresh notification checkpoint.
    No private address or Telegram credentials are written to state.
    """
    relevant = {
        "adapter": config["adapter"],
        "operator": config["operator"],
        "site_url": config["site_url"],
        "ajax_url": config["ajax_url"],
        "telegram_feed": config["telegram_feed"],
        "address": config["address"],
        "bot_token": bot_token,
        "chat_id": chat_id,
    }
    canonical = json.dumps(
        relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        config["state_key"].encode("utf-8"), canonical, hashlib.sha256,
    ).hexdigest()
