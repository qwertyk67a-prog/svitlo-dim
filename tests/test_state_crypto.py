"""No live values: authenticated state encryption with synthetic fixture keys."""
import base64
import json

import pytest

from scripts.repo_state import decode_checkpoint
from scripts.state_crypto import FORMAT, open_checkpoint, seal_checkpoint


def test_round_trip_encrypts_values_and_randomizes_nonces():
    state = {"house:groups": ["GPV3.1"], "config:namespace": "private-ref"}
    plain = json.dumps(state).encode("utf-8")
    a, b = seal_checkpoint(plain), seal_checkpoint(plain)
    assert a != b
    envelope = json.loads(a)
    assert envelope["format"] == FORMAT
    assert "GPV3.1" not in a.decode("ascii")
    assert "private-ref" not in a.decode("ascii")
    assert json.loads(open_checkpoint(a)) == state
    assert json.loads(open_checkpoint(b)) == state


def test_ciphertext_authentication_fails_closed():
    original = json.loads(seal_checkpoint(b'{"v":1}'))
    cipher = bytearray(base64.b64decode(original["ciphertext"]))
    cipher[0] ^= 1
    original["ciphertext"] = base64.b64encode(cipher).decode("ascii")
    with pytest.raises(RuntimeError, match="authentication failed"):
        open_checkpoint(json.dumps(original).encode("ascii"))


def test_wrong_installation_key_never_decrypts_state(monkeypatch):
    from conftest import TEST_CONFIG
    encrypted = seal_checkpoint(b'{"v":1}')
    config = json.loads(json.dumps(TEST_CONFIG))
    config["state_key"] = "fedcba9876543210" * 4
    monkeypatch.setenv("POWER_CONFIG_JSON", json.dumps(config))
    with pytest.raises(RuntimeError, match="authentication failed"):
        open_checkpoint(encrypted)


def test_cleartext_checkpoint_only_allowed_for_one_time_migration():
    old = b'{"v":1}'
    with pytest.raises(RuntimeError, match="Unencrypted checkpoint"):
        open_checkpoint(old)
    assert open_checkpoint(old, allow_legacy=True) == old
    assert open_checkpoint(b'{}') == b'{}'
    with pytest.raises(RuntimeError, match="object"):
        open_checkpoint(b'["not-a-checkpoint"]')


def test_repo_loader_rejects_plaintext_nonempty_remote():
    payload = {
        "sha": "revision",
        "content": base64.b64encode(b'{"house:groups":["GPV3.1"]}').decode(),
    }
    with pytest.raises(RuntimeError, match="Unencrypted checkpoint"):
        decode_checkpoint(payload)
    decoded, sha = decode_checkpoint(payload, allow_legacy=True)
    assert sha == "revision"
    assert decoded["house:groups"] == ["GPV3.1"]


def test_migration_writes_only_encrypted_checkpoint(monkeypatch):
    from scripts import migrate_checkpoint
    from scripts.state_crypto import open_checkpoint

    monkeypatch.setenv("GITHUB_REPOSITORY", "test-user/example-repo")
    monkeypatch.setenv("REPOSITORY_STATE_BRANCH", "bot-state")
    monkeypatch.setenv("REPOSITORY_STATE_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("STATE_PATH", "bot_state.json")
    monkeypatch.setenv("BOT_ENABLED", "false")
    monkeypatch.setenv("CONFIRM_MIGRATION", "MIGRATE")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-telegram")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100test")

    legacy = {"house:groups": ["GPV3.1"], "official:last_id": 123}
    calls = []

    def fake_call(repository, branch, token, payload=None):
        calls.append((branch, payload))
        if payload is not None:
            assert branch == "bot-state"
            raw = base64.b64decode(payload["content"])
            assert b"GPV3.1" not in raw
            data = json.loads(open_checkpoint(raw))
            assert data["official:last_id"] == 123
            assert data["config:namespace"]
            return {"content": {"sha": "written"}}
        state = legacy if branch == "main" else {}
        return {
            "sha": "existing",
            "content": base64.b64encode(json.dumps(state).encode()).decode(),
        }

    monkeypatch.setattr(migrate_checkpoint, "call_api", fake_call)
    migrate_checkpoint.run()
    assert [branch for branch, _ in calls] == [
        "main", "bot-state", "bot-state",
    ]
