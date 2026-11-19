"""Static checks for one-house GitHub workflow and secret boundaries."""
from pathlib import Path
import re
import subprocess

import yaml

ROOT=Path(__file__).resolve().parents[1]


def test_pinned_official_actions_and_checkout_has_no_persisted_credentials():
    for name in ("check.yml","tests.yml"):
        wf=yaml.safe_load((ROOT/".github/workflows"/name).read_text())
        job=next(iter(wf["jobs"].values()))
        actions=[step for step in job["steps"] if "uses" in step]
        assert actions
        assert all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}",s["uses"])
                   for s in actions)
        if name == "check.yml":
            assert actions[0]["with"]["persist-credentials"] is False


def test_read_step_does_not_get_state_push_token():
    wf=yaml.safe_load((ROOT/".github/workflows/check.yml").read_text())
    steps=wf["jobs"]["check"]["steps"]
    notify=next(s for s in steps if s.get("id")=="notify")
    save=next(s for s in steps if "Save notification state" in s.get("name",""))
    assert "GITHUB_STATE_PUSH_TOKEN" not in notify["env"]
    assert "GITHUB_STATE_PUSH_TOKEN" in save["env"]
    assert "TELEGRAM_BOT_TOKEN" not in save["env"]
    assert "DTEK_ADDRESS_JSON" not in save["env"]
    assert "persist-credentials: false" in (
        ROOT/".github/workflows/check.yml").read_text()
    assert "GIT_ASKPASS" in save["run"]
    assert 'git push "https://github.com/$GITHUB_REPOSITORY.git" HEAD:main' in save["run"]
    result=subprocess.run(["bash","-n"],input=save["run"],text=True,
                          capture_output=True,check=False)
    assert result.returncode == 0, result.stderr


def test_both_secrets_only_referenced_by_name():
    wf=(ROOT/".github/workflows/check.yml").read_text()
    assert "secrets.TELEGRAM_BOT_TOKEN" in wf
    assert "secrets.DTEK_ADDRESS_JSON" in wf
    for filename in ("bot.py","alerts.py","house_reliability.py"):
        content=(ROOT/filename).read_text(encoding="utf-8")
        assert "TELEGRAM_BOT_TOKEN" not in content or (
            'os.getenv("TELEGRAM_BOT_TOKEN"' in content)
        assert "https://api.telegram.org/bot" not in content or filename=="bot.py"


def test_single_house_does_not_implement_user_enumeration():
    content="\n".join(
        (ROOT/name).read_text(encoding="utf-8")
        for name in ("bot.py","alerts.py","house_reliability.py")
    )
    for forbidden in ("getChatAdministrators","getChatMemberCount",
                      "getUpdates","setWebhook","sendDocument","sendPhoto"):
        assert forbidden not in content


def test_verify_address_invoked_as_module_and_never_sends_telegram():
    """Direct script execution loses repository-root imports in Actions."""
    workflow=(ROOT/".github/workflows/verify-address.yml").read_text()
    assert "python -m scripts.verify_address" in workflow
    assert "python scripts/verify_address.py" not in workflow
    assert "secrets.DTEK_ADDRESS_JSON" in workflow
    assert "DTEK_WAF_BROWSER" in workflow
    assert "secrets.TELEGRAM_BOT_TOKEN" not in workflow
    import importlib
    verifier=importlib.import_module("scripts.verify_address")
    assert callable(verifier.main)


def test_manual_dry_run_workflow_has_no_telegram_or_write_access():
    content = (ROOT / ".github/workflows/audit-dry-run.yml").read_text()
    assert "workflow_dispatch" in content
    assert "contents: read" in content
    assert "TELEGRAM_BOT_TOKEN" not in content
    assert "TELEGRAM_CHAT_ID" not in content
    assert 'DRY_RUN: "true"' in content
    assert "DTEK_ADDRESS_JSON" in content


def test_dependencies_pinned_against_compatible_constraints():
    requirements = (ROOT / "requirements.txt").read_text()
    assert "-c constraints.txt" in requirements
    assert "requests==" in requirements
    assert "beautifulsoup4==" in requirements
    assert "dtek-blackout-client==" in requirements
    assert "playwright==" in (ROOT / "constraints.txt").read_text()


def test_telegram_setup_is_manual_read_only_and_does_not_activate_monitoring():
    content=(ROOT/".github/workflows/telegram-setup.yml").read_text()
    assert "workflow_dispatch" in content
    assert "contents: read" in content
    assert "secrets.TELEGRAM_BOT_TOKEN" in content
    assert "secrets.TELEGRAM_CHAT_ID" in content
    assert "DTEK_ADDRESS_JSON" not in content
    assert "BOT_ENABLED" not in content
    assert "python -m scripts.telegram_setup" in content
    assert "sendMessage" not in content
