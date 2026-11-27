"""Isolated GitHub state is revision-controlled and never writes main."""
import base64
import json
from urllib.error import HTTPError
import pytest

from scripts import repo_state
from scripts.state_crypto import open_checkpoint, seal_checkpoint


def test_checkpoint_read_save_uses_state_branch_only(monkeypatch, tmp_path):
    local = tmp_path / "state.json"
    monkeypatch.setenv("GITHUB_REPOSITORY", "test-user/svitlo-example")
    monkeypatch.setenv("REPOSITORY_STATE_BRANCH", "bot-state")
    monkeypatch.setenv("REPOSITORY_STATE_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("STATE_PATH", "state.json")
    monkeypatch.chdir(tmp_path)
    visited = []
    remote = {"config:namespace": "opaque", "v": 1}

    class Response:
        def __init__(self, content):
            self.content = content
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self, *_args):
            return json.dumps(self.content).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        visited.append((request.method, request.full_url,
                        request.get_header("Authorization")))
        if request.method == "GET":
            return Response({
                "sha": "blob-current",
                "content": base64.b64encode(
                    seal_checkpoint(json.dumps(remote).encode("utf-8"))
                ).decode(),
            })
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["branch"] == "bot-state"
        assert payload["sha"] == "blob-current"
        assert json.loads(open_checkpoint(base64.b64decode(payload["content"])))["v"] == 2
        assert b'"v"' not in base64.b64decode(payload["content"])
        return Response({"content": {"sha": "blob-new"}})

    monkeypatch.setattr(repo_state, "urlopen", fake_urlopen)
    repo_state.load()
    assert json.loads(local.read_text(encoding="utf-8")) == remote
    remote_local = json.loads(local.read_text(encoding="utf-8"))
    remote_local["v"] = 2
    local.write_text(json.dumps(remote_local), encoding="utf-8")
    repo_state.save()
    assert [p[0] for p in visited] == ["GET", "GET", "PUT"]
    assert "?ref=bot-state" in visited[0][1]
    assert "?ref=" not in visited[-1][1]
    assert all(v[2] == "Bearer fake-token-for-test" for v in visited)
    assert all("bot_state.json" in v[1] for v in visited)


@pytest.mark.parametrize("lost_reply", [False, True])
def test_identical_checkpoint_is_not_written(monkeypatch, tmp_path, lost_reply):
    monkeypatch.setenv("GITHUB_REPOSITORY", "test-user/svitlo-example")
    monkeypatch.setenv("REPOSITORY_STATE_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("STATE_PATH", "state.json")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state.json").write_text('{"v":2}', encoding="utf-8")
    calls = []
    def fake_call(repository, branch, token, payload=None):
        calls.append(payload)
        if lost_reply and len(calls) == 1:
            return {"sha":"old", "content":base64.b64encode(b"{}").decode()}
        if payload is not None:
            raise TimeoutError("lost PUT reply")
        return {"sha":"blob", "content":base64.b64encode(seal_checkpoint(b'{"v":2}')).decode()}
    monkeypatch.setattr(repo_state, "call_api", fake_call)
    (tmp_path / "state.json.revision").write_text("old")
    monkeypatch.setattr(repo_state.time, "sleep", lambda _: None)
    repo_state.save()
    assert [item is None for item in calls] == ([True, False, True] if lost_reply else [True])


def test_conflicting_checkpoint_refuses_to_overwrite(monkeypatch,tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "test-user/svitlo-example")
    monkeypatch.setenv("REPOSITORY_STATE_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("STATE_PATH", "state.json")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state.json").write_text('{"v":2}', encoding="utf-8")
    calls = []
    def fake_call(repository, branch, token, payload=None):
        calls.append(payload)
        if payload is not None:
            raise RuntimeError("GitHub checkpoint API HTTP 409")
        return {"sha":"old", "content":base64.b64encode(seal_checkpoint(b'{"v":1}')).decode()}
    monkeypatch.setattr(repo_state, "call_api", fake_call)
    (tmp_path / "state.json.revision").write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError, match="409"):
        repo_state.save()
    assert len(calls) == 2


def test_bad_checkpoint_is_rejected():
    with pytest.raises(RuntimeError, match="object"):
        repo_state.decode_checkpoint({
            "sha":"abc", "content":base64.b64encode(b'["not", "a", "dict"]').decode()
        })


def test_missing_remote_state_does_not_fall_back_to_empty(monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError("http://example.invalid",404,"not found",{},None)
    monkeypatch.setattr(repo_state,"urlopen",missing)
    with pytest.raises(RuntimeError, match="HTTP 404"):
        repo_state.call_api("test-user/svitlo-example","bot-state","test-token")


def test_changed_remote_since_load_is_preserved(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "test-user/example")
    monkeypatch.setenv("REPOSITORY_STATE_TOKEN", "test-token")
    monkeypatch.setenv("STATE_PATH", "state.json")
    revision = {"sha": "first", "content": base64.b64encode(b"{}").decode()}

    def read_only(repository, branch, token, payload=None):
        assert payload is None
        return revision

    monkeypatch.setattr(repo_state, "call_api", read_only)
    repo_state.load()
    (tmp_path / "state.json").write_text('{"local": 1}', encoding="utf-8")
    revision.update(sha="second", content=base64.b64encode(
        seal_checkpoint(b'{"other": 1}')).decode())
    with pytest.raises(RuntimeError, match="changed since load"):
        repo_state.save()
