"""House aliases must not conflate distinct numeric building identifiers."""
from types import SimpleNamespace

import pytest

from alerts import get_house_entry, normalized_house


@pytest.mark.parametrize("alias", [
    "42А", "42а", "42 A", "42-А", "42‐А", "42‑А", "42—А",
    "№ 42А", "буд. № 42А", "４２Ａ",
])
def test_typographic_suffix_variants_match_exact_house(alias):
    assert normalized_house(alias) == normalized_house("42А")


@pytest.mark.parametrize("alias", ["42/А", "42.А", "42／А"])
def test_unique_provider_letter_separator_is_accepted(alias):
    entry = object()
    response = SimpleNamespace(houses={alias: entry})
    assert get_house_entry(response, "42А") is entry


def test_two_provider_letter_separator_candidates_are_ambiguous():
    response = SimpleNamespace(houses={"42/А": object(), "42.А": object()})
    with pytest.raises(RuntimeError, match="однозначно"):
        get_house_entry(response, "42А")


@pytest.mark.parametrize("other", [
    "421", "42/1", "42.1", "42/А/1", "42А/1", "42Б", "42А1",
])
def test_numeric_extensions_are_not_house_suffix_aliases(other):
    assert normalized_house(other) != normalized_house("42А")
    response = SimpleNamespace(houses={other: object()})
    with pytest.raises(RuntimeError, match="однозначно"):
        get_house_entry(response, "42А")


def test_exact_provider_number_wins_over_potential_suffix_alias():
    exact, variant = object(), object()
    response = SimpleNamespace(houses={"42А": exact, "42/А": variant})
    assert get_house_entry(response, "42А") is exact
