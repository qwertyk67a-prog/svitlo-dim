"""Read-only verification that migrated private history decrypts on Actions.

No actual addresses, queue identifiers, tokens or history keys are logged.
Never contacts Telegram and never writes to GitHub.
"""
import os
from pathlib import Path

from bot import state_read
from power_config import checkpoint_namespace, provider_config
from scripts.repo_state import load


def verify():
    load()
    path = Path(os.getenv("STATE_PATH", "bot_state.json"))
    data = state_read(path)
    if not isinstance(data, dict) or not data:
        raise RuntimeError("Migrated checkpoint is missing")
    expected = checkpoint_namespace(
        provider_config(), "", os.getenv("TELEGRAM_CHAT_ID", "").strip()
    )
    if data.get("config:namespace") != expected:
        raise RuntimeError("Migrated checkpoint identity does not match")
    if not isinstance(data.get("house:groups"), list) or not data["house:groups"]:
        raise RuntimeError("Migrated official queue assignment is missing")
    if not any(key[:10].count("-") == 2 for key in data):
        raise RuntimeError("Migrated schedule deduplication history missing")
    print("Encrypted checkpoint verification: OK")
    print("Preserved logical state records:", len(data))


if __name__ == "__main__":
    try:
        verify()
    except Exception as exc:
        print("Encrypted checkpoint verification failed:", type(exc).__name__)
        raise SystemExit(1) from None
