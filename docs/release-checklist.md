# Safe deployment checklist

This is an operational checklist. The code is **not fully deployed** until the
owner installs and verifies the private configuration for the selected source.

## A. Prepare without interrupting the existing bot

1. Keep the existing main-branch monitor running.
2. Open **Settings → Secrets and variables → Actions → Repository secrets**.
3. Create **POWER_CONFIG_JSON**, using the example schema in README.md.
   Transfer the address from the previously configured secret inside GitHub.
   Include operator, official site, same-host AJAX endpoint, public Telegram
   announcement channel, city/region grammatical forms and negative filters.
4. Include the full **queue_catalog** for the operator's selected adapter.
   Do not fill a single assumed queue from the old apartment.
5. Generate a unique **state_key** locally:
   `python -c "import secrets; print(secrets.token_hex(32))"`
   Paste only the 64-character result into the new GitHub Secret.
6. Keep the existing **TELEGRAM_BOT_TOKEN** and **TELEGRAM_CHAT_ID** secrets.
   Do not print or paste their contents into Issues, code or workflow inputs.

## B. Read-only preflight on the feature branch

1. In GitHub Actions open **Verify configured address**.
2. Choose **Run workflow → feature/provider-independent-template-20261120**.
3. Confirm one or more verified queues and that schedule retrieval succeeded.
4. Open **Dry-run source audit (no Telegram)**.
5. Select the same feature branch and run once.
6. Require **Success** on both workflows. Review the logs for errors.
7. Neither workflow should send Telegram messages or write a checkpoint.
8. If the configured provider does not speak the ajax_v1 protocol, **stop**.
   Another adapter requires new code and validated live-source tests.

## C. Switch the active installation

1. Ensure the new secret is complete. Before merging, set
   **Repository variable BOT_ENABLED=false** to stop the old live job.
2. Merge the reusable-template PR only after both read-only preflights pass.
3. The old public bot_state.json is removed by the template change. New
   pseudonymized checkpoints use a new installation namespace.
4. Check that the existing Telegram bot and destination are correct.
5. Re-enable **BOT_ENABLED=true**.
6. Manually run **Check power schedule** on main, confirm GitHub Actions
   reports success and the correct schedule reaches the desired destination.
7. Review that bot_state.json contains anonymized queue references.
8. Review the next scheduled run to check that unchanged notices stay silent.

## D. Duplicate for another property

1. Copy the repository into a different public GitHub repository.
2. Create completely new POWER_CONFIG_JSON, TELEGRAM_BOT_TOKEN and
   TELEGRAM_CHAT_ID secrets; repository secrets never transfer.
3. Generate a **different** state_key for each property and destination.
4. Start with BOT_ENABLED=false, then repeat read-only preflight.
5. Do not reuse a previous property's bot_state.json or queue_catalog.
6. Use the same ajax_v1 adapter only for confirmed compatible sources.
   Different API formats need separate, tested adapters.

## Limitations

- Old publicly committed values remain in Git history.
- Public checkpoint data are pseudonymized, not encrypted.
- No paid storage, server or encryption package is required.
- Standard public-repository GitHub Actions runners are free, subject to
  service limits and GitHub policies.
- Scheduled GitHub Actions may be delayed or skipped. Telegram may deliver
  a message before a network timeout; exactly-once delivery is impossible
  with this checkpoint design.
- The owner must install GitHub Secrets. The assistant cannot access or
  create repository Secrets using the current GitHub connection.
