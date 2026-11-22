"""Health checks use synthetic GitHub runs and never send Telegram."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from scripts import monitor_health


NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def run(minutes_ago, *, outcome="success", branch="main", event="workflow_dispatch"):
    return {
        "status": "completed",
        "conclusion": outcome,
        "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "head_branch": branch,
        "event": event,
    }


def test_recent_success_is_healthy_even_after_a_single_failed_check():
    assert monitor_health.analyze([
        run(5, outcome="failure"), run(7, outcome="success")
    ], NOW) == 7 * 60


def test_last_success_older_than_twenty_minutes_is_failed():
    with pytest.raises(RuntimeError, match="within 20 minutes"):
        monitor_health.analyze([
            run(5, outcome="failure"), run(21, outcome="success"),
        ], NOW)


def test_nonproduction_success_does_not_hide_failed_production():
    with pytest.raises(RuntimeError, match="No completed successful"):
        monitor_health.analyze([
            run(1, branch="feature/test-branch"),
            run(3, outcome="failure"),
        ], NOW)


def test_empty_or_incomplete_run_history_is_not_assumed_healthy():
    with pytest.raises(RuntimeError, match="trustworthy"):
        monitor_health.analyze(None, NOW)
    with pytest.raises(RuntimeError, match="No completed successful"):
        monitor_health.analyze([{"status": "queued"}], NOW)


def test_intentionally_disabled_monitor_is_not_a_health_alarm(monkeypatch):
    monkeypatch.setenv("BOT_ENABLED", "false")
    monkeypatch.setattr(
        monitor_health, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("Unexpected network request"),
    )
    monitor_health.main()


def test_live_health_run_uses_github_api_without_secrets_in_log(monkeypatch, capsys):
    monkeypatch.setenv("BOT_ENABLED", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "sample-org/sample-repo")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-monitor-token")
    monkeypatch.setattr(monitor_health, "MAX_AGE_SECONDS", 20 * 60)
    now = datetime.now(timezone.utc)
    value = {
        "workflow_runs": [{
            "status": "completed",
            "conclusion": "success",
            "created_at": now.isoformat(),
            "head_branch": "main",
            "event": "workflow_dispatch",
        }],
    }

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def read(self, *_args):
            return json.dumps(value).encode()

    def fake_urlopen(request, timeout=None):
        assert request.get_header("Authorization") == "Bearer fake-monitor-token"
        assert "branch=main" in request.full_url
        return Response()

    monkeypatch.setattr(monitor_health, "urlopen", fake_urlopen)
    monitor_health.main()
    out = capsys.readouterr().out
    assert "Health assessment: OK" in out
    assert "fake-monitor-token" not in out


@pytest.mark.parametrize("value", ["", "True", "typo"])
def test_missing_or_invalid_activation_is_visible(monkeypatch, value):
    monkeypatch.setenv("BOT_ENABLED", value)
    with pytest.raises(RuntimeError, match="Invalid BOT_ENABLED"):
        monitor_health.main()
