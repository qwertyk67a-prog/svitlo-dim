# Post-change technical audit

Audit date: 2026-11-20
Scope: reusable branch, not the currently active default branch.

## Source and address privacy

- Operator name, official site, AJAX endpoint, public announcement feed,
  city, region, districts, quarters and house address are obtained from
  the POWER_CONFIG_JSON repository secret.
- Telegram bot token and destination remain in their own repository secrets.
- No default operational queue is hardcoded. Queue identities are returned
  by the chosen address API and verified against private queue_catalog.
- A public code scan found no former company, locality, house number or
  regional feed literals in working modules, tests and documentation.
- Old Git history is unchanged. No private repository or paid storage is used.
- The public state file is not encrypted. Queue IDs are HMAC-pseudonymized
  with state_key; stored notices contain only metadata and digests.
  Previously committed state remains in Git history.
- Public diagnostic scripts no longer print chat IDs or bot usernames.
  During manual group discovery, the chat ID is returned inside that
  Telegram group itself.

## Portability

- Synthetic tests confirm a second city, operator, official Telegram feed,
  address, group formats and private state-key rollover.
- A list of valid queue names must be supplied in the private configuration.
- Only the compatible form-encoded AJAX adapter is implemented.
  Other company APIs are NOT guaranteed to be compatible simply by replacing
  a URL. Unsupported adapter names fail closed.

## Transport and data quality

- Signed Yii-style CSRF cookies are bootstrapped before the actual query.
- CSRF header tokens use a fresh XOR mask and can be refreshed once.
- HTTP 429 does not launch the browser fallback.
- Other direct-source failures may use cached Chromium when available.
- Address lookup rejects ambiguous building numbers and numeric slash
  collisions; a successful response with no known queue does not invent one.
- Daily official facts take priority over preliminary weekly tables.
- Unknown or unreadable schedule cells are not treated as cancellations.
- Place matching uses word boundaries to prevent a city name from matching
  the beginning of a different regional name.
- A regional message is not proof of actual electricity loss at one home.
  Geographic forms and negative-place filters must be supplied correctly.

## Checkpoint, delivery and reliability

- Only queue identity components are pseudonymized in checkpoint keys.
  Date strings, reminder times and digest contents remain unchanged.
- A changed address or source configuration starts a new checkpoint namespace.
- Successful Telegram notifications are checkpointed individually.
- A timeout after Telegram accepted a message may still produce a duplicate.
- GitHub Actions schedule is best effort, so five-minute accuracy cannot
  be guaranteed. Public repositories using standard runners are free,
  but other GitHub limits still apply.

## Automated verification

GitHub Actions Python suite passed: **188 tests** on this branch.
Includes mock CSRF sequences, invalid configs, synthetic multi-city switches,
queue changes, regional name-boundary matching, initial deployments,
retry/deduplication, and public
checkpoint safety checks.

This is not a live-source acceptance test. The new POWER_CONFIG_JSON secret
has not been installed or validated against the actual target operator in
this branch. Real website parser validity, queue assignments, source
availability and Telegram delivery require read-only preflight workflows
with the owner's actual GitHub Actions secrets.

## Release decision

Do not merge the branch into the active default branch until the real
POWER_CONFIG_JSON repository secret is installed and the manual
**Verify configured address** and **Dry-run source audit** workflows succeed.
Keep the currently working installation running in the meantime.

## Namespace stability

The installation checkpoint now uses canonicalized operational identity, not raw JSON bytes. Whitespace and JSON key ordering do not reset notification history. Different properties, Telegram destinations or bot credentials remain independent. This needs a first-run checkpoint migration when enabling the new configuration.

## Final audit update

- Static review checked 36 current public text files: no old fixed operator, city, house, district, quarter or queue identifier matched.
- Canonicalized checkpoint namespaces preserve sent-message history across JSON whitespace and key-order changes.
- Changing the property, Telegram token or destination creates a distinct checkpoint namespace.
- A 64-character hex state key is required. Syntax validation cannot prove randomness; use a cryptographically secure generator.
- GitHub Actions unit run 44766285064 passed 188 tests before the documentation-only release checklist was added.
- A live acceptance run with the new POWER_CONFIG_JSON is still required before merging to the active main branch.

## 21 November 2026 corrective implementation

The earlier independent-geometry evaluator was removed. Paid geocoding is not
needed for routine queue checks. The district and historical quarter in the
private config are used only to filter explicit official territory notices.
This is a stated geographic claim, not automatic GIS proof.

- House number typographical aliases now cover Unicode dashes, spacing and
  Latin look-alike suffixes. Numeric suffixes and multiple matches fail closed.
- The browser session fetched during an official provider fallback is reused
  for subsequent queries in the same process. Rate-limited requests are not
  retried through the browser.
- Isolated `bot-state` branch created. Live monitoring code on the feature
  branch no longer commits checkpoints into `main`.
- A manual one-time `migrate-checkpoint.yml` workflow preserves eligible
  old notification history and writes only pseudonymized data to `bot-state`.
  It cannot run while BOT_ENABLED=true and will not overwrite nonempty state.
- Cloudflare Free scheduler is a **scaffold only**. It is not deployed,
  connected or activated. It needs a separate Cloudflare account and a
  restricted Actions:write GitHub token. Five-minute trigger is not an
  end-to-end delivery promise.
- **219 tests passed** in GitHub Actions run 44917592362 for all code and
  migration logic. Documentation edits followed without runtime code changes.
- Re-run live no-Telegram preflights before any production cutover.
- Old public Git history still exposes historical checkpoint metadata.
