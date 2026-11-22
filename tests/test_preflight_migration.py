"""Read-only real-state migration preflight regression tests."""
import json

import pytest

from power_config import provider_config
from scripts import preflight_migration


def test_preview_preserves_real_notification_keys_without_sending():
    legacy = {
        "house:groups": ["GPV3.1"],
        "2026-11-22:GPV3.1": {"status": "outages", "digest": "test-only"},
        "official:last_id": 77,
        "official:status:emergency:region": {"status": "active"},
    }
    old_count, new_count = preflight_migration.preview(
        legacy, provider_config(), "-100test"
    )
    assert old_count == 4
    assert new_count == 5


def test_preflight_refuses_unknown_or_malformed_queue_catalog():
    config = provider_config()
    with pytest.raises(RuntimeError, match="absent from private catalog"):
        preflight_migration.preview(
            {"house:groups": ["GPV_NOT_A_REAL_QUEUE"]}, config, "-100test"
        )
    with pytest.raises(RuntimeError, match="absent from private catalog"):
        preflight_migration.preview(
            {"house:groups": "GPV3.1"}, config, "-100test"
        )


def test_preflight_refuses_missing_chat_or_invalid_history():
    config = provider_config()
    with pytest.raises(RuntimeError, match="Incomplete"):
        preflight_migration.preview({}, config, "")
    with pytest.raises(RuntimeError, match="Incomplete"):
        preflight_migration.preview([], config, "-100test")


def test_read_only_preflight_does_not_mutate_input():
    source = {
        "house:groups": ["GPV3.1"],
        "official:post_hashes": {"10": "abc"},
        "official:last_id": 10,
    }
    before = json.dumps(source, sort_keys=True)
    preflight_migration.preview(source, provider_config(), "-100test")
    assert json.dumps(source, sort_keys=True) == before
