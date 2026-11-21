# Safe release checklist (no paid services)

The current main-branch bot is live. Do not merge this feature branch until
all checks pass. The linked GitHub connector cannot update repository
Secrets/Variables, create a restricted PAT, or deploy Cloudflare.

## 1. Completed preparatory checks

- POWER_CONFIG_JSON was installed and accepted by the feature-branch address
  check and the source audit on 20 November.
- The new source adapter and session reuse passed mocked integration tests.
- Private geography does not require map API calls.
- A GitHub branch `bot-state` now exists with an empty JSON checkpoint.
- The feature-branch `check.yml` reads and updates only that isolated branch.
- Cloudflare dispatcher code is ready but is not deployed or activated.

## 2. Optional free scheduling, without changing main

1. Follow `deploy/cloudflare-dispatcher/README.md`.
2. Use Cloudflare **Workers Free**, never Workers Paid.
3. Set Worker variables GH_REPOSITORY, GH_REF and ENABLED in Cloudflare.
4. Store a restricted GitHub Actions:write token as GH_WORKFLOW_TOKEN in
   Cloudflare Worker Secrets only.
5. Activate the five-minute Cron Trigger and verify its GitHub
   workflow_dispatch jobs for 24 hours.
6. This only accelerates triggering; GitHub-runner start delays remain.

The dispatcher can target old `main` before the feature merge. Keep the
existing GitHub native cron as a backup until reliability is measured.

## 3. New version read-only acceptance

1. Run **Verify configured address (no Telegram)** on
   `feature/provider-independent-template-20261120`.
2. Run **Dry-run source audit (no Telegram)** on the same branch.
3. Require both to succeed; check the same verified queue and official data.
4. These calls do not send Telegram messages or overwrite the checkpoint.
5. If the service or configured address is ambiguous, do not switch.

## 4. Freeze, migrate and switch (manual short outage)

1. In Settings → Secrets and variables → Actions → Variables, set
   `BOT_ENABLED=false`. Confirm the next scheduler run takes the inactive
   branch and sends nothing.
2. On the feature branch, run the manual workflow
   **Migrate notification checkpoint (no Telegram)** with confirmation
   exactly `MIGRATE`. It reads legacy `main/bot_state.json` and writes an
   HMAC-anonymized checkpoint to `bot-state` once, without sending messages.
3. Confirm the migration workflow is green. It refuses to overwrite a
   nonempty target branch or run with BOT_ENABLED=true.
4. Refresh the feature branch against the now-frozen `main`; the old active
   bot's repeated state commits may otherwise make PR #17 conflict.
5. Merge PR #17 into main only after the above checks.
6. Confirm that main no longer stores `bot_state.json` on every check.
7. Set `BOT_ENABLED=true`. Manually run **Check power schedule** on main.
8. Confirm the real Telegram chat receives only new/changed notices and the
   bot-state checkpoint updates. Check another run with no schedule change.
9. Confirm that native GitHub and Cloudflare scheduled runs do not overlap,
   repeat messages or exceed expected frequency.
10. Never paste tokens, precise addresses or raw state into public issues/logs.

## 5. Follow-up monitoring

- Track the last successful check and time since the last attempted check.
- Distinguish source failure, queue lookup failure, Telegram delivery failure
  and state-write failure. A green run does not prove power is available.
- Retain event deduplication after deployment; state migration reduces first-
  run duplicates, but Telegram cannot guarantee exactly-once delivery.
- Audit historical public state commits separately. They cannot be erased
  by moving future state to a new branch.
- If external scheduling still does not give acceptable delays, use a
  continuously running backend. Do not promise strict five-minute delivery.

## 6. Duplicate for a second property

- Clone the reusable repo without copying a prior bot-state history.
- Create new POWER_CONFIG_JSON, bot token, chat ID and random state_key.
- Create a new empty isolated state branch, run preflight, then activate.
- Only the ajax_v1 provider protocol is implemented. Other utility companies
  require tested adapters, not just different URLs.
