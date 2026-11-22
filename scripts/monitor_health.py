"""Read-only health assessment of the live GitHub Actions outage monitor.

The job does not use Telegram credentials, private addresses, or state tokens.
It reports a failing Actions job when no recent successful monitor run exists.
This check shares GitHub infrastructure and is NOT an independent watchdog.
"""
import json
import os
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen


MAX_AGE_SECONDS = 20 * 60


def parse_time(raw):
    if not isinstance(raw, str):
        raise RuntimeError("Missing workflow run timestamp")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("Naive timestamp")
        return value.astimezone(timezone.utc)
    except ValueError:
        raise RuntimeError("Invalid workflow run timestamp") from None


def analyze(runs, now, max_age_seconds=MAX_AGE_SECONDS):
    """Fail closed on missing history and prolonged lack of successful checks."""
    if not isinstance(runs, list):
        raise RuntimeError("No trustworthy workflow history")
    completed = [
        run for run in runs
        if isinstance(run, dict) and run.get("status") == "completed"
        and run.get("head_branch") == "main"
        and run.get("event") in ("workflow_dispatch", "schedule")
    ]
    successful = [run for run in completed if run.get("conclusion") == "success"]
    if not successful:
        raise RuntimeError("No completed successful power monitor check")
    last = max(successful, key=lambda run: parse_time(run.get("created_at")))
    last_started = parse_time(last.get("created_at"))
    seconds = (now.astimezone(timezone.utc) - last_started).total_seconds()
    if seconds < -60:
        raise RuntimeError("GitHub reported a future workflow check")
    if seconds > max_age_seconds:
        raise RuntimeError("Power monitor has no successful run within 20 minutes")
    return int(max(0, seconds))


def main():
    activation = os.getenv("BOT_ENABLED", "").strip()
    if activation == "false":
        print("Health check skipped: production bot intentionally disabled")
        return
    if activation != "true":
        raise RuntimeError("Invalid BOT_ENABLED setting")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("GITHUB_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        raise RuntimeError("Invalid health-check configuration")
    url = (
        "https://api.github.com/repos/" + repo +
        "/actions/workflows/check.yml/runs?per_page=40&branch=main"
    )
    request = Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "svitlo-health-check",
    })
    try:
        with urlopen(request, timeout=15) as response:
            data = json.load(response)
    except Exception:
        # Never reveal the Authorization header through exception text.
        raise RuntimeError("GitHub monitor history unavailable") from None
    if not isinstance(data, dict):
        raise RuntimeError("GitHub monitor history malformed")
    age = analyze(data.get("workflow_runs"), datetime.now(timezone.utc))
    print("Last successful production monitor check age (seconds):", age)
    print("Health assessment: OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Health assessment failed:", str(exc))
        raise SystemExit(1) from None
