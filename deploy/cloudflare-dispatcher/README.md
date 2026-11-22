# Cloudflare Free: five-minute GitHub workflow dispatcher

This dispatcher is **already deployed and active** for the production bot.
Keep `ENABLED=true`. The optional external watchdog is disabled until its
private Telegram destination and KV binding are configured. The original
GitHub fine-grained token stays a Cloudflare Runtime Secret.

## Why

Native GitHub scheduled workflows have been observed starting about once
per hour despite a five-minute cron. A separate free Cloudflare Cron Trigger
sends a workflow_dispatch request every five minutes. GitHub may still queue
a run. Neither delivery nor exact five-minute execution is guaranteed.

At one request every five minutes, the Worker executes 288 times/day.
This is within Cloudflare Workers Free's published 100,000 requests/day,
assuming no unrelated account workloads exceed the allowance.

## Deployment without paid features

1. Sign in to Cloudflare and choose **Workers Free**. Do not add a paid plan.
2. Use Workers Builds → Connect to GitHub.
3. Select repository `qwertyk67a-prog/svitlo-dim`, branch
   `main`, root directory
   `deploy/cloudflare-dispatcher`. Set deployment command
   `npx wrangler deploy`.
4. The Wrangler project name is `svitlo-schedule-dispatcher`.
5. In GitHub, create a fine-grained personal access token scoped **only** to
   this repository and with **Actions: write** permission. Choose the
   shortest practical expiration. Never publish the token.
6. Under the Worker Settings → Variables and Secrets add:
   - Plain variable `GH_REPOSITORY` = `qwertyk67a-prog/svitlo-dim`.
   - Plain variable `GH_REF` = `main`.
   - Plain variable `ENABLED` = `true` only when ready to begin dispatches.
   - **Encrypted secret** `GH_WORKFLOW_TOKEN` = your scoped GitHub token.
7. Verify the Cron Trigger `*/5 * * * *` is present. Cloudflare notes a
   propagation delay of up to 15 minutes.
8. In GitHub Actions, confirm new runs with event `workflow_dispatch`.
   Check that the existing `BOT_ENABLED` gate allows intentional monitoring.
9. Record actual run intervals for one day. If gaps persist, improve the
   backend architecture rather than claiming five-minute guarantees.

The Worker has no HTTP request handler and contains no Telegram secrets,
property address or electricity-source credentials. Keep these only in
existing GitHub repository Secrets. The GitHub token exists **only** inside
Cloudflare's encrypted secrets.

## Deployment safety

- GitHub's existing concurrency lock prevents simultaneous checks of this repo.
- The native GitHub cron can remain as a fallback. Duplicate checks are not
  equivalent to duplicate announcements, but delivery after ambiguous
  network failures can still repeat.
- Do **not** reset the encrypted `bot-state` branch. PR #17 has already been
  merged; the active `main` monitor uses encrypted notification checkpoints.
- Keep Cloudflare Workers Builds on `main`. The Worker dispatches `check.yml`
  independently of the monitor's power-provider parser.
- The free Worker is a lightweight dispatcher. Python, Playwright and
  state storage remain on GitHub.

Official documentation:
https://developers.cloudflare.com/workers/ci-cd/builds/git-integration/github-integration/
https://developers.cloudflare.com/workers/ci-cd/builds/configuration/
https://developers.cloudflare.com/workers/configuration/cron-triggers/
https://developers.cloudflare.com/workers/platform/pricing/

The Cloudflare GitHub installation may be authorized for **all repositories**, which is acceptable for a dedicated GitHub account. This does **not** create a Worker or GitHub API credential. Keep the **new fine-grained Actions:write token** scoped to this repository, even when Cloudflare's GitHub installation can access other repositories.

## Independent external watchdog — prepared, not enabled

GitHub-based health checks cannot warn when GitHub itself is unavailable.
The dispatcher can separately inspect GitHub's recent completed workflow
runs every five minutes and notify a **private administrator Telegram chat**.
This watchdog runs on Cloudflare, not GitHub. It does not report physical
power availability and does not transmit the monitored street address.

**Safe default:** `WATCHDOG_ENABLED = "false"` in `wrangler.toml`.
The existing Cron dispatch remains active while the watchdog is disabled.

To activate later, prepare all three Cloudflare resources first:

1. Create a free Workers KV namespace, for example `svitlo-watchdog-state`.
   Copy its namespace ID. Add its binding **to the deployment's
   `wrangler.toml`** only after creation:

   ```toml
   [[kv_namespaces]]
   binding = "WATCHDOG_KV"
   id = "REPLACE_WITH_THE_ACTUAL_NAMESPACE_ID"
   ```

   **Never commit the placeholder ID.** A real namespace ID is required
   before deployment. A dashboard-only binding may not survive subsequent
   Wrangler deployments; keep Wrangler and dashboard settings consistent.
2. Create a **dedicated administrator Telegram bot**, or select a trusted
   existing bot. Add its token to the *Cloudflare runtime Secrets* as
   `WATCHDOG_TELEGRAM_BOT_TOKEN`. Never use a plain-text Variable.
3. Start a private chat with that bot. Put the numeric chat ID into a
   *Cloudflare runtime Secret* called `WATCHDOG_TELEGRAM_CHAT_ID`.
   Do not send tokens in screenshots, issues or ChatGPT messages.
4. Verify Cloudflare has the `WATCHDOG_KV` binding and both Secrets.
   Only then change `WATCHDOG_ENABLED` to `"true"` in Wrangler and
   deploy. Monitor the first external health assessment and a simulated
   failure before relying on it.

### Alert rules

- A completed, successful `check.yml` run on `main` during the preceding
  20 minutes counts as healthy. Source failures that mark the GitHub job
  unsuccessful do not count.
- Two consecutive unhealthy five-minute checks trigger one administrator
  warning. A transient single GitHub API failure is not reported.
- Ongoing faults trigger at most one reminder per 60 minutes in normal KV
  operation. A successful check after an alert triggers one recovery notice.
- If the GitHub PAT expires, the watchdog retries **public** GitHub run
  history without using that token. Dispatch still requires a valid PAT.
- If Telegram delivery fails, the watchdog retries at the next check;
  failed notifications are not marked as delivered.
- Cloudflare KV is eventually consistent. Rare duplicate administrator
  warnings are possible during a concurrent invocation. They do not alter
  production Telegram notification history.
- If Cloudflare itself is unavailable, the watchdog cannot report its own
  failure. This is not a guaranteed safety service.

### Free-tier use

The existing five-minute Cron executes about 288 times per day. With the
watchdog enabled, KV normally performs at most 288 reads per day and writes
only when the health status changes or an alert is sent. Current Workers KV
Free limits include 100,000 reads/day and 1,000 writes/day.

Cloudflare documentation:
https://developers.cloudflare.com/kv/platform/limits/
https://developers.cloudflare.com/kv/get-started/
https://developers.cloudflare.com/workers/configuration/cron-triggers/
