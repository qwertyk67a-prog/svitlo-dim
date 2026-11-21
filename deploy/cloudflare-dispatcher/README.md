# Optional Cloudflare five-minute dispatcher

**Not activated.** Existing GitHub monitoring remains unchanged.

GitHub documents that native scheduled workflows can be delayed or omitted. A
Cloudflare Cron Trigger can call the existing workflow-dispatch API every five
minutes, independently of GitHub's cron scheduler. GitHub runners can still queue,
and exactly-five-minute polling and notification delivery are not guaranteed.

## Configuration

1. Create a Cloudflare Worker with the code in src/index.js and the five-minute
   cron trigger from wrangler.toml. Use Cloudflare's official dashboard or CLI.
2. Add Worker variables GH_REPOSITORY (owner/name), GH_REF (main),
   and ENABLED (initially false).
3. Create a **fine-grained GitHub personal access token**, restricted to this
   repository and **Actions: write** permission only.
4. Store the PAT as Cloudflare Worker **Secret GH_WORKFLOW_TOKEN**.
5. Check current bot delivery and serialized runs in GitHub first.
6. Set ENABLED=true and verify incoming workflow_dispatch runs in GitHub Actions.
7. Measure real run intervals for at least 24 hours before declaring improved
   scheduling. Do not promise strict five-minute delivery to residents.

The Worker has no fetch() event handler and no externally accessible HTTP
endpoint. It stores no property details, provider URL or Telegram credentials.
Do not log the GitHub token and do not commit any real secret.

The Github Actions workflow also checks BOT_ENABLED and uses one concurrency
group. An external dispatch may overlap a native scheduled event. Digest-based
suppression reduces ordinary duplicate notifications but cannot guarantee
exactly-once Telegram delivery after an ambiguous network timeout.

Keep the native GitHub cron as a fallback unless duplicate checks overwhelm
the electricity provider. If strict bounded polling is required, use a
continuously running server with persistent state rather than workflow dispatch.

See:
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
