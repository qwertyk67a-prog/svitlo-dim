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
from urllib.request import Request, urlopen

from bot import state_write
from power_config import checkpoint_namespace, provider_config
from scripts.repo_state import call_api, decode_checkpoint, settings
from scripts.state_crypto import seal_checkpoint


def migrate_snapshot(raw, namespace):
    if not isinstance(raw, dict) or not isinstance(namespace, str):
        raise RuntimeError("Invalid source state")
    safe = {}
    for key, value in raw.items():
        allowed = (
            key in {"house:groups", "official:last_id",
                    "official:post_hashes", "address:last_outage"}
            # Active GAV / emergency regime must survive migration. Keeping
            # only the feed cursor would otherwise discard an ongoing outage.
            or key.startswith("official:status:")
            or key.startswith("official:incident:")
            or key.startswith("official:unresolved:")
            or key.startswith("official:event:")
            or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}:.+", key))
            or bool(re.fullmatch(r"(?:week|reminder):\d{4}-\d{2}-\d{2}:.+", key))
        )
        if allowed:
            safe[key] = value
    safe["config:namespace"] = namespace
    return safe



def live_monitor_is_frozen(repository, token):
    """Require a verified pause in the CURRENT production workflow.

    A temporary GitHub Actions gate allows a controlled upgrade even when
    repository variables cannot be edited by the deployment connector.
    Never trust a feature-branch marker as proof that main is paused.
    """
    url = (
        "https://api.github.com/repos/" + repository +
        "/contents/.github/workflows/check.yml?ref=main"
    )
    request = Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "svitlo-release-freeze-check",
    })
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.load(response)
        raw = base64.b64decode(payload["content"], validate=False)
        text = raw.decode("utf-8")
    except Exception:
        raise RuntimeError("Production pause cannot be verified") from None
    required = (
        'SVITLO_RELEASE_PAUSE: "true"',
        'if [[ "$SVITLO_RELEASE_PAUSE" == "true" ]]; then',
        'echo "active=false" >> "$GITHUB_OUTPUT"',
        'SVITLO_RELEASE_PAUSE_V1: no Telegram notifications during migration',
    )
    first = text.find('if [[ "$SVITLO_RELEASE_PAUSE" == "true" ]]; then')
    normal = text.find('if [[ "$CONFIGURED_BOT_ENABLED"')
    if (any(item not in text for item in required)
            or first < 0 or normal < 0 or first >= normal
            or 'exit 0' not in text[first:normal]):
        raise RuntimeError("Production workflow is not explicitly paused")
    return True


def run():
    activation = os.getenv("BOT_ENABLED", "").strip().lower()
    if activation not in ("true", "false"):
        raise RuntimeError("Disable the old live monitor explicitly before migration")
    if os.getenv("CONFIRM_MIGRATION") != "MIGRATE":
        raise RuntimeError("Explicit manual confirmation required")
    repository, branch, token, _path = settings()
    if activation == "true" and not live_monitor_is_frozen(repository, token):
        raise RuntimeError("Disable the old live monitor before migration")
    if branch != "bot-state":
        raise RuntimeError("Unexpected target checkpoint branch")
    config = provider_config()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Missing Telegram delivery identity")
    namespace = checkpoint_namespace(config, bot_token, chat_id)

    old_state, _sha = decode_checkpoint(
        call_api(repository, "main", token), allow_legacy=True
    )
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
        "message": "Migrate encrypted notification history [skip ci]",
        "branch": branch,
        "sha": target_sha,
        "content": base64.b64encode(seal_checkpoint(data)).decode("ascii"),
    }
    call_api(repository, branch, token, payload)
    print("Checkpoint migration completed; no Telegram notifications sent")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("Migration failed:", type(exc).__name__)
        raise SystemExit(1) from None
