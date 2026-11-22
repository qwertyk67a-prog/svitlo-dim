"""Read-only preview of real main notification history migration.

Fetch the legacy checkpoint using a read-only GitHub token. Validate the
new namespace, queue catalog, HMAC anonymization and encrypted round trip.
Never write back to GitHub or call Telegram. Print counts only.
"""
import base64
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen

from bot import state_read, state_write
from power_config import checkpoint_namespace, provider_config
from scripts.migrate_checkpoint import migrate_snapshot
from scripts.repo_state import decode_checkpoint
from scripts.state_crypto import open_checkpoint, seal_checkpoint


def fetch_legacy(repository, token):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("Invalid repository name")
    if not token:
        raise RuntimeError("GitHub read-only token unavailable")
    req = Request(
        "https://api.github.com/repos/" + repository +
        "/contents/bot_state.json?ref=main",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "svitlo-migration-preflight",
        },
    )
    try:
        with urlopen(req, timeout=20) as response:
            payload = json.load(response)
    except Exception:
        raise RuntimeError("Legacy checkpoint cannot be fetched") from None
    result, _sha = decode_checkpoint(payload, allow_legacy=True)
    return result


def preview(raw, config, chat):
    """Check exact restoration from the future encrypted branch format."""
    if not isinstance(raw, dict) or not isinstance(chat, str) or not chat:
        raise RuntimeError("Incomplete migration preview inputs")
    catalog = set(config["queue_catalog"])
    groups = raw.get("house:groups", [])
    if not isinstance(groups, list) or any(g not in catalog for g in groups):
        raise RuntimeError("Legacy house queues absent from private catalog")
    namespace = checkpoint_namespace(config, "", chat)
    moved = migrate_snapshot(raw, namespace)
    with tempfile.TemporaryDirectory() as dirname:
        path = Path(dirname) / "state.json"
        state_write(path, moved)
        pseudonymized = path.read_bytes()
        assert state_read(path) == moved
        encrypted = seal_checkpoint(pseudonymized)
        assert b'"house:groups"' not in encrypted
        decrypted = open_checkpoint(encrypted)
        path.write_bytes(decrypted)
        if state_read(path) != moved:
            raise RuntimeError("Encrypted migrated checkpoint cannot be restored")
    return len(raw), len(moved)


def main():
    config = provider_config()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    state = fetch_legacy(
        os.getenv("GITHUB_REPOSITORY", ""),
        os.getenv("GITHUB_TOKEN", ""),
    )
    source_count, target_count = preview(state, config, chat)
    print("Migration preflight: PASSED")
    print("Legacy record count:", source_count)
    print("Preserved record count:", target_count)
    print("No Telegram messages or remote state writes were made")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Migration preflight failed:", type(exc).__name__)
        raise SystemExit(1) from None
