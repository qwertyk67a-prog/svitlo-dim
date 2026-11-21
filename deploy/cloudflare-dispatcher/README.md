# Cloudflare Free: five-minute GitHub workflow dispatcher

This deploy has **not** been activated. The existing Telegram monitor stays
operational. A Cloudflare account connection and restricted GitHub token are
necessary, and cannot be created by the linked GitHub connector.

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
- Do **not** reset old state or merge PR #17 merely to enable this scheduler.
  It can dispatch the existing `check.yml` in `main` first.
- Keep Cloudflare Workers Builds on `main`. The Worker is independent of
  whether PR #17 is merged.
- The free Worker is a lightweight dispatcher. Python, Playwright and
  state storage remain on GitHub.

Official documentation:
https://developers.cloudflare.com/workers/ci-cd/builds/git-integration/github-integration/
https://developers.cloudflare.com/workers/ci-cd/builds/configuration/
https://developers.cloudflare.com/workers/configuration/cron-triggers/
https://developers.cloudflare.com/workers/platform/pricing/

The Cloudflare GitHub installation may be authorized for **all repositories**, which is acceptable for a dedicated GitHub account. This does **not** create a Worker or GitHub API credential. Keep the **new fine-grained Actions:write token** scoped to this repository, even when Cloudflare's GitHub installation can access other repositories.
