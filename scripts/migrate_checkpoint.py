"""One-time non-sending migration from legacy main checkpoint.

Run only AFTER old BOT_ENABLED is false and BEFORE merging the new monitor.
Never log the source address, queue identifiers, Telegram credentials or state.
"""
import base64
import json
import os
from pathlib import Path
import re
import tempfile

from bot import state_write
from power_config import checkpoint_namespace, provider_config
from scripts.repo_state import call_api, decode_checkpoint, settings


def migrate_snapshot(raw, namespace):
    if not isinstance(raw, dict) or not isinstance(namespace, str):
        raise RuntimeError("Invalid source state")
    safe = {}
    for key, value in raw.items():
        allowed = (
            key in {"house:groups", "official:last_id",
                    "official:post_hashes", "address:last_outage"}
            or key.startswith("official:event:")
            or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}:.+", key))
            or bool(re.fullmatch(r"(?:week|reminder):\d{4}-\d{2}-\d{2}:.+", key))
        )
        if allowed:
            safe[key] = value
    safe["config:namespace"] = namespace
    return safe


def run():
    if os.getenv("BOT_ENABLED", "").strip().lower() == "true":
        raise RuntimeError("Disable the old live monitor before migration")
    if os.getenv("CONFIRM_MIGRATION") != "MIGRATE":
        raise RuntimeError("Explicit manual confirmation required")
    repository, branch, token, _path = settings()
    if branch != "bot-state":
        raise RuntimeError("Unexpected target checkpoint branch")
    config = provider_config()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Missing Telegram delivery identity")
    namespace = checkpoint_namespace(config, bot_token, chat_id)

    old_state, _sha = decode_checkpoint(call_api(repository, "main", token))
    existing, target_sha = decode_checkpoint(call_api(repository, branch, token))
    if existing:
        raise RuntimeError("Target state not empty: refuse overwrite")

    # Serialize through the production HMAC queue anonymization.
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory) / "checkpoint.json"
        state_write(local, migrate_snapshot(old_state, namespace))
        data = local.read_bytes()
    public = json.loads(data)
    if not isinstance(public, dict) or public.get("config:namespace") != namespace:
        raise RuntimeError("Migrated state did not retain identity")

    payload = {
        "message": "Migrate anonymized notification history [skip ci]",
        "branch": branch,
        "sha": target_sha,
        "content": base64.b64encode(data).decode("ascii"),
    }
    call_api(repository, branch, token, payload)
    print("Checkpoint migration completed; no Telegram notifications sent")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("Migration failed:", type(exc).__name__)
        raise SystemExit(1) from None
