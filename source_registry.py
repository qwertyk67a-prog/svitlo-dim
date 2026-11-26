"""Read a pinned public operator directory as data. Never execute remote code."""
import ast
import hashlib
from urllib.parse import urlsplit

import requests

REGISTRY_URL = ("https://raw.githubusercontent.com/fastiuk/ukraine-energy-monitor/"
                "af18fe2dac4a0132e7e6ea8e8608b643e3640b70/src/ua_outages/registry.py")
REGISTRY_SHA256 = "f25146dc0154cf099e873b03010053c7fd78eb969e615dec14f95f2540cbda2a"


def match_sources(document, site, operator):
    host = urlsplit(site).hostname
    for node in ast.walk(ast.parse(document)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Operator" and len(node.args) >= 4):
            continue
        label, page = node.args[1], node.args[3]
        if not (isinstance(label, ast.Constant) and label.value.casefold() == operator.casefold()
                and isinstance(page, ast.Constant) and urlsplit(page.value).hostname == host):
            continue
        sources = next((kw.value for kw in node.keywords if kw.arg == "sources"), None)
        result = {}
        for entry in sources.elts if isinstance(sources, ast.Tuple) else ():
            if not isinstance(entry, ast.Call) or not isinstance(entry.func, ast.Name):
                continue
            if entry.func.id == "Yasno":
                ids = {item.arg: item.value.value for item in entry.keywords
                       if isinstance(item.value, ast.Constant) and type(item.value.value) is int}
                if ids.keys() >= {"region", "dso"}:
                    result["official_reserve_url"] = (
                        f"https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/{ids['region']}"
                        f"/dsos/{ids['dso']}/planned-outages")
            if (entry.func.id == "OutageDataUa" and len(entry.args) == 1 and not entry.keywords
                    and isinstance(entry.args[0], ast.Constant)):
                # A second argument is a provider-specific queue translation.
                result["mirror_region"] = entry.args[0].value
        return result
    return {}


def reserve_settings(config):
    # Never send the installation settings or house address to the registry.
    if config.get("official_reserve_url") or config.get("mirror_region"):
        return config
    response = requests.get(REGISTRY_URL, timeout=6, allow_redirects=False)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != REGISTRY_SHA256:
        raise ValueError("Unverified operator directory")
    return match_sources(response.content, config["site_url"], config["operator"])
