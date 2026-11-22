# Power-outage monitor: one property, one Telegram destination

This is a reusable public repository. Real addresses, company endpoints,
city/region names and official channel identifiers belong only in Secrets.

## Provider protocol

The ajax_v1 adapter understands a particular form-encoded schedule API.
Other energy suppliers may use different schemas. Changing just a URL is
NOT proof of compatibility. Add and test a new adapter before activation.
Unknown providers fail closed.

## Required GitHub Actions Repository Secrets

POWER_CONFIG_JSON: all operator, source and property settings (example below).
TELEGRAM_BOT_TOKEN: a bot token from BotFather.
TELEGRAM_CHAT_ID: a Telegram group or channel ID.

Example POWER_CONFIG_JSON schema (SYNTHETIC details only):

~~~json
{
  "adapter": "ajax_v1",
  "operator": "ПрикладЕнерго",
  "queue_catalog": ["EXAMPLE_1", "EXAMPLE_2"],
  "queue_display_prefix": "EXAMPLE_",
  "state_key": "REPLACE_WITH_64_RANDOM_HEX_CHARACTERS",
  "site_url": "https://grid.example.invalid/schedule",
  "ajax_url": "https://grid.example.invalid/ajax",
  "telegram_feed": "https://t.me/s/sample_energy",
  "city_label": "Зелений Бір",
  "city_forms": ["зелений бір", "зеленого бору", "зеленому борі"],
  "region_label": "Прикладна область",
  "region_forms": ["прикладщина", "прикладна область", "прикладній області", "прикладної області"],
  "headline_prefixes": ["прикладенерго"],
  "other_places": ["іншоград", "другоград", "південний гай"],
  "other_regions": ["сусідня область"],
  "address": {
    "city": "м. Зелений Бір",
    "street": "вул. Умовна",
    "house": "12Г",
    "district": "Зразковий район",
    "quarter": "987 квартал"
  }
}
~~~

Replace all values with real details only INSIDE the GitHub Secret.
The location arrays must contain grammatical forms used in official notices.
Wrong forms may cause missed or irrelevant announcements.
The district and quarter are optional. Only city, street and house are sent
to the provider address API.

Repository variables: BOT_ENABLED=true enables monitoring.
REMINDER_MINUTES=0 disables reminders by default.
ANNOUNCEMENTS_ENABLED=true enables official regional announcements.

## Setup

1. Add the full POWER_CONFIG_JSON Repository Secret.
2. Run Verify configured address (no Telegram).
3. Run Dry-run source audit (no Telegram).
4. Run Connect Telegram bot (manual) in test mode.
5. Check one Telegram test message.
6. Enable BOT_ENABLED=true and manually run Check power schedule.

One copied repository needs one separate set of Secrets and one destination.
GitHub does not copy Secrets when cloning a repository. Keep BOT_ENABLED
disabled until the address, API and test delivery are verified.

## Automatic queue changes

The private queue_catalog lists all valid group IDs for this supplier.
The queue_display_prefix controls how each ID appears to residents.
A complete catalog is necessary to restore anonymized checkpoints.

No fixed queue is stored in the production code. The address API returns
one or several verified queues. Changes are announced relative to the last
confirmed set, never to a guessed default. Uncertain address data do not
trigger new queue assignments or false power restoration.

## State and security

The `bot-state` GitHub branch stores an **AES-256-GCM encrypted**
notification checkpoint. The encryption key is derived locally from the
random `state_key` inside private `POWER_CONFIG_JSON`. No plaintext history,
private address, or encryption key is stored in new `bot-state` commits.
The repository starts with an empty `bot-state` branch. The one-time manual
migration reads legacy `main/bot_state.json` only after the old monitor is
disabled, and writes authenticated ciphertext without sending Telegram.

Keep `state_key` unchanged across routine configuration edits, token rotation,
and migration. Changing it makes the existing ciphertext unreadable without
the previous key; do not change it without a planned offline re-encryption.
Use a distinct random key for each unrelated property or copied repository.
The state is briefly plaintext on the isolated GitHub Actions runner. Prior
public commits on `main` are **not** erased by encrypting future history.
Only repo administrators and trusted workflow code may access Secrets.

Encryption uses the existing free GitHub Actions service and the pinned
open-source Python `cryptography` package; no paid service is required.

GitHub Actions scheduling is best-effort; exact five-minute updates and
exactly-once Telegram delivery are not guaranteed.

Generate your individual state_key **locally** using:

~~~bash
python -c "import secrets; print(secrets.token_hex(32))"
~~~

Paste the resulting 64 hexadecimal characters only into your GitHub Secret. Never copy an example key. The format validator cannot prove that an accepted key is truly random.

## Before deploying this version

See [Safe deployment checklist](docs/release-checklist.md). The new POWER_CONFIG_JSON secret must be installed, then the feature-branch address check and dry-run must both succeed. Do not merge solely on unit tests. The current active main-branch bot remains unchanged until the release checks pass.

## Free scheduler and locality scope

The existing native GitHub cron can run late. The optional Cloudflare Worker
in `deploy/cloudflare-dispatcher` dispatches checks every five minutes on
Cloudflare Workers Free, without geocoding or hosting the Python monitor.
The Worker is **not deployed or enabled** until the owner connects Cloudflare
and adds an Actions:write GitHub token as a Worker Secret.

The provider's official address directory determines the house's queue.
District/quarter names from private configuration are used only when an
official notice explicitly targets those areas. They are not independently
verified geographic boundaries and do not block queue-based schedules.
No recurring Google Maps / paid geocoding is used.

See `docs/release-checklist.md` for the exact safe cutover sequence.
