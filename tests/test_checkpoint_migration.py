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
    monkeypatch.setattr(migrate_checkpoint, "settings", lambda:
                        ("example/repo", "bot-state", "fake-token", Path("state.json")))
    monkeypatch.setattr(migrate_checkpoint, "live_monitor_is_frozen",
                        lambda *_args: False)
    with pytest.raises(RuntimeError, match="Disable"):
        migrate_checkpoint.run()


def test_migration_refuses_missing_or_ambiguous_enabled_flag(monkeypatch):
    import pytest
    from scripts import migrate_checkpoint
    monkeypatch.setenv("CONFIRM_MIGRATION", "MIGRATE")
    monkeypatch.setattr(migrate_checkpoint, "settings", lambda:
                        ("example/repo", "bot-state", "fake-token", Path("state.json")))
    monkeypatch.setattr(migrate_checkpoint, "live_monitor_is_frozen",
                        lambda *_args: False)
    for disabled in ("", "true", "False true", "0"):
        monkeypatch.setenv("BOT_ENABLED", disabled)
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
    config = json.loads((root / "deploy/cloudflare-dispatcher/worker-template.json").read_text())
    assert 'env.ENABLED !== "true"' in worker
    assert config["triggers"]["crons"] == ["*/5 * * * *"]
    assert "GH_WORKFLOW_TOKEN" in worker
    assert "ghp_" not in worker
    assert config["vars"]["ENABLED"] == "true"


def test_main_release_freeze_requires_actual_production_workflow_marker(monkeypatch):
    import base64
    from scripts import migrate_checkpoint

    paused = '''
name: Check power schedule
          SVITLO_RELEASE_PAUSE: "true"
          if [[ "$SVITLO_RELEASE_PAUSE" == "true" ]]; then
            echo "active=false" >> "$GITHUB_OUTPUT"
            echo "SVITLO_RELEASE_PAUSE_V1: no Telegram notifications during migration"
            exit 0
          fi
          if [[ "$CONFIGURED_BOT_ENABLED" =~ ^[[:space:]]*true[[:space:]]*$ ]]; then
'''
    class Response:
        def __init__(self, content):
            self.content = content
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, *_args):
            return json.dumps({
                "content": base64.b64encode(self.content.encode()).decode()
            }).encode()

    monkeypatch.setattr(migrate_checkpoint, "urlopen",
                        lambda *_args, **_kwargs: Response(paused))
    assert migrate_checkpoint.live_monitor_is_frozen("example/repo", "token")

    monkeypatch.setattr(migrate_checkpoint, "urlopen",
                        lambda *_args, **_kwargs: Response(
                            paused.replace('SVITLO_RELEASE_PAUSE: "true"',
                                           'SVITLO_RELEASE_PAUSE: "false"')
                        ))
    import pytest
    with pytest.raises(RuntimeError, match="not explicitly paused"):
        migrate_checkpoint.live_monitor_is_frozen("example/repo", "token")
