"""Authenticated encryption for checkpoints stored in a public Git branch.

The state key exists only in the private POWER_CONFIG_JSON repository secret.
The cleartext may exist temporarily in the ephemeral Actions runner, never in
bot-state commits. Do not print encryption inputs or authentication failures.
"""
import base64
import hashlib
import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from power_config import provider_config


FORMAT = "svitlo-aesgcm-v1"
AAD = b"svitlo/bot-state/v1"
MAX_CLEAR_BYTES = 256 * 1024


def _key():
    # Domain separation: an HMAC pseudonym key is never used directly as AES.
    root = bytes.fromhex(provider_config()["state_key"])
    return hashlib.sha256(b"svitlo/state-encryption/v1\x00" + root).digest()


def seal_checkpoint(clear: bytes) -> bytes:
    if not isinstance(clear, bytes) or len(clear) > MAX_CLEAR_BYTES:
        raise RuntimeError("Checkpoint size exceeds safe encryption limit")
    try:
        state = json.loads(clear)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("Invalid checkpoint object") from None
    if not isinstance(state, dict):
        raise RuntimeError("Checkpoint must be a JSON object")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, clear, AAD)
    envelope = {
        "format": FORMAT,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return (json.dumps(envelope, separators=(",", ":"), sort_keys=True) +
            "\n").encode("ascii")


def open_checkpoint(payload: bytes, *, allow_legacy=False) -> bytes:
    if not isinstance(payload, bytes) or len(payload) > MAX_CLEAR_BYTES * 2:
        raise RuntimeError("Checkpoint too large")
    try:
        parsed = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("Unrecognized checkpoint format") from None
    if not isinstance(parsed, dict):
        raise RuntimeError("Checkpoint must be a JSON object")

    # Only an exactly empty bootstrap state may remain plaintext in bot-state.
    if parsed == {}:
        return b"{}"
    if parsed.get("format") != FORMAT:
        if allow_legacy:
            return payload  # one-time read from old main, never written back
        raise RuntimeError("Unencrypted checkpoint refused")
    try:
        nonce = base64.b64decode(parsed["nonce"], validate=True)
        ciphertext = base64.b64decode(parsed["ciphertext"], validate=True)
        if len(nonce) != 12 or len(ciphertext) < 16:
            raise ValueError("Invalid envelope lengths")
        clear = AESGCM(_key()).decrypt(nonce, ciphertext, AAD)
        if len(clear) > MAX_CLEAR_BYTES or not isinstance(json.loads(clear), dict):
            raise ValueError("Invalid decrypted checkpoint")
        return clear
    except (KeyError, TypeError, ValueError, InvalidTag, UnicodeDecodeError):
        raise RuntimeError(
            "Checkpoint authentication failed or encryption key changed"
        ) from None
