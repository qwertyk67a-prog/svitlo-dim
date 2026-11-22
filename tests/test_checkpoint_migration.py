"""Migration preserves delivery history but never blindly adopts foreign identity."""
import json
from pathlib import Path

from bot import state_write, state_read, _queue_tokens
from power_config import provider_config, checkpoint_namespace
from scripts.migrate_checkpoint import migrate_snapshot


def test_migration_removes_unknown_fields_and_previous_namespace(tmp_path):
    legacy = {
        "config:namespace": "OLD-OTHER-ADDRESS",
        "house:groups": ["GPV3.1"],
        "2026-11-21:GPV3.1": {"digest": "abc", "status": "outages"},
        "week:2026-11-16:GPV3.1": {"digest": "weekly"},
        "reminder:2026-11-21:GPV3.1:480:15": {"sent": True},
        "official:last_id": 124,
        "official:post_hashes": {"121": "abc"},
        "official:status:emergency:region": {"status": "active", "published": "2026-11-21T14:00:00+02:00"},
        "official:incident:gav:region:opaque": {"status": "active", "published": "2026-11-21T15:00:00+02:00"},
        "unknown:secret": "never-copy",
    }
    new = migrate_snapshot(legacy, "new-private-identity")
    assert new["config:namespace"] == "new-private-identity"
    assert new["house:groups"] == ["GPV3.1"]
    assert "unknown:secret" not in new
    assert new["2026-11-21:GPV3.1"]["digest"] == "abc"
    assert new["official:last_id"] == 124
    assert new["official:status:emergency:region"]["status"] == "active"
    assert new["official:incident:gav:region:opaque"]["status"] == "active"

    file = tmp_path / "state.json"
    state_write(file, new)
    public = file.read_text(encoding="utf-8")
    assert "GPV3.1" not in public
    assert "never-copy" not in public
    assert "OLD-OTHER-ADDRESS" not in public
    assert "new-private-identity" in public
    assert state_read(file) == new


def test_migration_identity_matches_new_delivery_identity():
    config = provider_config()
    identity = checkpoint_namespace(config, "token-for-test", "-100test")
    state = migrate_snapshot({}, identity)
    assert state["config:namespace"] == identity
    # Credential rotation must not reset delivered-notification history.
    assert checkpoint_namespace(config, "rotated-token", "-100test") == identity
    # A real destination change must use a different notification history.
    assert checkpoint_namespace(config, "rotated-token", "-200test") != identity


def test_migration_refuses_to_run_while_monitor_active(monkeypatch):
    import pytest
    from scripts import migrate_checkpoint
    monkeypatch.setenv("BOT_ENABLED", "true")
    monkeypatch.setenv("CONFIRM_MIGRATION", "MIGRATE")
    with pytest.raises(RuntimeError, match="Disable"):
        migrate_checkpoint.run()


def test_migration_requires_explicit_confirmation(monkeypatch):
    import pytest
    from scripts import migrate_checkpoint
    monkeypatch.setenv("BOT_ENABLED", "false")
    monkeypatch.delenv("CONFIRM_MIGRATION", raising=False)
    with pytest.raises(RuntimeError, match="confirmation"):
        migrate_checkpoint.run()


def test_external_dispatcher_requires_explicit_activation():
    root = Path(__file__).resolve().parents[1]
    worker = (root / "deploy/cloudflare-dispatcher/src/index.js").read_text()
    wrangler = (root / "deploy/cloudflare-dispatcher/wrangler.toml").read_text()
    assert 'env.ENABLED !== "true"' in worker
    assert 'crons = ["*/5 * * * *"]' in wrangler
    assert "GH_WORKFLOW_TOKEN" in worker
    assert "ghp_" not in worker
    assert 'ENABLED = "true"' in wrangler
