"""Persist notification checkpoints on a separate GitHub branch.

Only the read/write steps receive GITHUB_TOKEN. The source code branch is
never modified by routine monitoring. Failed or conflicting writes are fatal.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from scripts.state_crypto import open_checkpoint, seal_checkpoint


MAX_BYTES = 512 * 1024


def settings():
    repository = os.getenv("GITHUB_REPOSITORY", "")
    branch = os.getenv("REPOSITORY_STATE_BRANCH", "bot-state")
    token = os.getenv("REPOSITORY_STATE_TOKEN", "")
    path = Path(os.getenv("STATE_PATH", "bot_state.json"))
    if (len(repository.split("/")) != 2
            or not all(part.replace("-", "").replace("_", "").replace(".", "").isalnum()
                       for part in repository.split("/"))
            or not token or not branch or "/" in branch or ".." in branch):
        raise RuntimeError("Invalid repository state configuration")
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("Invalid state path")
    return repository, branch, token, path


def call_api(repository, branch, token, payload=None):
    url = ("https://api.github.com/repos/" + repository
           + "/contents/bot_state.json?ref=" + quote(branch, safe=""))
    method = "GET"
    if payload is not None:
        url = url.split("?")[0]
        method = "PUT"
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if payload is not None else None,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "svitlo-state-checkpoint",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise RuntimeError("Checkpoint response too large")
            return json.loads(data)
    except HTTPError as exc:
        if exc.code in (500, 502, 503, 504):
            raise ConnectionError("Temporary checkpoint service failure") from None
        # Never echo API body. It may reflect unexpected data.
        raise RuntimeError("GitHub checkpoint API HTTP " + str(exc.code)) from None


def decode_checkpoint(payload, *, allow_legacy=False):
    if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str):
        raise RuntimeError("GitHub checkpoint missing revision")
    raw = payload.get("content")
    if not isinstance(raw, str):
        raise RuntimeError("GitHub checkpoint missing contents")
    try:
        data = base64.b64decode(raw, validate=False)
        if len(data) > MAX_BYTES:
            raise ValueError("too large")
        clear = open_checkpoint(data, allow_legacy=allow_legacy)
        state = json.loads(clear.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Malformed GitHub notification checkpoint") from None
    if not isinstance(state, dict):
        raise RuntimeError("GitHub notification checkpoint must be object")
    return state, payload["sha"]


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            name = output.name
            output.write(content)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def load():
    repository, branch, token, path = settings()
    state, sha = decode_checkpoint(call_api(repository, branch, token))
    write_atomic(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    write_atomic(path.with_name(path.name + ".revision"), sha)
    print("Checkpoint loaded from isolated state branch")


def save():
    try:
        _save()
    except (URLError, TimeoutError, ConnectionError):
        # Read back before retrying: a lost PUT response can hide a successful write.
        time.sleep(2)
        _save()


def _save():
    repository, branch, token, path = settings()
    if not path.is_file():
        raise RuntimeError("Missing checkpoint: refusing to overwrite remote state")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise RuntimeError("Checkpoint too large")
    try:
        state = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("Malformed local checkpoint") from None
    if not isinstance(state, dict):
        raise RuntimeError("Local checkpoint must be object")
    remote, sha = decode_checkpoint(call_api(repository, branch, token))
    if remote == state:
        print("Checkpoint unchanged")
        return
    revision_path = path.with_name(path.name + ".revision")
    if not revision_path.is_file() or revision_path.read_text(encoding="utf-8") != sha:
        raise RuntimeError("Remote checkpoint changed since load or base revision missing")
    payload = {
        "message": "Store isolated notification checkpoint [skip ci]",
        "branch": branch,
        "sha": sha,
        "content": base64.b64encode(seal_checkpoint(
            (json.dumps(state, ensure_ascii=False, indent=2) + "\n")
            .encode("utf-8")
        )).decode("ascii"),
    }
    # GitHub's sha-based precondition rejects concurrent writes. Never retry
    # with another base revision: doing so could discard delivered notices.
    call_api(repository, branch, token, payload)
    print("Checkpoint saved on isolated state branch")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("load", "save"):
        raise SystemExit("Usage: python -m scripts.repo_state load|save")
    try:
        (load if sys.argv[1] == "load" else save)()
    except Exception as exc:
        print("Checkpoint operation failed:", type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
