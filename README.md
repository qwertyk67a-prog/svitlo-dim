# Power-outage monitor: one property, one Telegram destination

This is a reusable public repository. Real addresses, company endpoints,
city/region names and official channel identifiers belong only in Secrets.

## Provider protocol

Select the adapter inside the existing POWER_CONFIG_JSON secret. The current
ajax_v1 setting keeps the original operator site, address API and Telegram feed.

For official HTTPS GET JSON feeds, select mapped_json_v1. Set site_url to the
address directory and ajax_url to the schedule feed. The feeds can use
different hostnames. Keep telegram_feed for regional bulletins. Add a mapping
object to the same secret. This example uses synthetic field names:

~~~json
{
  "addresses": ["directory"], "city": ["city"], "street": ["street"],
  "house": ["building"], "queues": ["groups"],
  "labels": ["periods"], "days": ["schedules"], "date": ["date"],
  "queue": ["group"], "slots": ["availability"],
  "statuses": {"available": "yes", "outage": "no"}
}
~~~

Paths are lists of JSON keys. An empty path selects the whole document.
Address rows must identify city, street, house and queue(s). Daily rows must
identify a local ISO date, one queue and a complete dictionary of slots.
The labels dictionary maps each slot key to a time range covering part of
the day. All ranges together must cover the whole day. Map each source status
explicitly. Optional address paths sub_type, start_date, end_date and type
supply outage details. Optional weekly selects a group/weekday/slots dictionary.
Source links in Telegram use the site origin by default. Set public_site_url
in POWER_CONFIG_JSON to show a specific public information page. The private
request path and query are never reused as the default link.
Both feeds must be accessible without authentication. Unsupported formats
require another reader. Verify address and dry-run before changing delivery.

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

Repository Secrets: BOT_ENABLED=true enables monitoring. Existing installations also accept previous repository variables.
REMINDER_MINUTES=0 disables reminders.
ANNOUNCEMENTS_ENABLED=true enables official regional announcements.

## Setup

1. Copy only the current main files into a new repository with a main branch. Add the three required property Secrets. Run Initialize empty notification history once.
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
Initialize empty notification history creates a separate branch with an empty checkpoint in a new repository. The one-time manual
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

## Independent scheduler

Follow [Cloudflare setup](deploy/cloudflare-dispatcher/README.md) for the dispatcher and its private administrator watchdog. Every installation needs a separate Telegram destination, encryption key and state. A new repository must have Actions enabled. Keep the default branch named main.

When a supplier has no public Telegram bulletin channel, omit telegram_feed
and set the repository Secret ANNOUNCEMENTS_ENABLED to false. Address queries and daily schedules support either adapter. With ajax_v1,
site_url and ajax_url must share a hostname. With mapped_json_v1, supply
a compatible mapping and public HTTPS JSON feeds, which may use different hosts. Verify the actual house and daily schedule using the
read-only workflows before activating a copy.

Both Telegram bots can have any username. Manual discovery accepts group
commands. Delivery test mode accepts an explicit numeric private, group,
supergroup or channel ID. Administrator delivery uses the private numeric
chat ID in the Worker runtime Secrets.

### Telegram schedule board

The production check maintains one separate pinned schedule. Ordinary alerts
remain in the chat history. Allow the bot to pin messages in the group.
Resident messages and pins allow sound from 10:00 until local sunset or 20:00, whichever comes first (Europe/Kyiv).
The bot calculates sunset offline for the configured Ukrainian city. An unresolved city keeps notifications silent.
Send /now in the configured chat, or privately as a verified group member.
The regular monitoring job reads commands, so replies may take one polling
cycle. Missing or incomplete source data are labelled on the saved schedule.

The private watchdog reports two consecutive failed schedule-pin checks separately from runner health. It reports recovery after a confirmed pin. Missing observations do not prove failure or recovery.

### House delivery settings

The scheduled installation uses the Actions variable `REMINDER_MINUTES` (20 when unset).
Set it to 0 to disable. The former Actions secret of the same name no longer controls this workflow.
The pin and /now can be switched independently through TELEGRAM_SCHEDULE_BOARD and NOW_COMMAND_ENABLED (the board defaults on and /now defaults off).
Local executions still use the environment setting and default to 0.

`PERSONAL_SUBSCRIPTIONS_ENABLED=true` enables the private Telegram command /subscriptions.
Default off. Residents can subscribe to a house after verification against that house's Telegram group.
Additional houses of this same operator can be listed in the private configuration under
`subscription_homes` as objects with `label`, `chat` (the house group ID), and `address`.
Use only houses covered by the installation's configured emergency-announcement territory.
Each address requires a separate official queue lookup. The bot rechecks group membership before personal delivery.
Subscriptions send updated schedules. Group reminders use the existing group delivery channel.

`UNOFFICIAL_FALLBACK_ENABLED=true` enables a supplemental board section if the official daily schedule request fails.
The installation matches a pinned public registry by operator name and official host. Only direct queue mappings qualify. Private installations can instead supply reviewed settings.
The mirror cannot confirm an address assignment, cancellation, weekly forecast, or reminder.
The supplemental section expires unless a fresh successful copy is available.
Personal subscriptions default off. The scheduled installation enables the reserve policy by default. Set UNOFFICIAL_FALLBACK_ENABLED=false to disable registry discovery and the unofficial mirror.

For local incident replay, save only the provider schedule fields `preset` and `fact`.
Run `python -m scripts.replay_schedule snapshot.json QUEUE YYYY-MM-DD`.
An optional `official_reserve_url` selects the verified YASNO planned-outages endpoint for the same distribution operator.
This official daily reserve always runs before an enabled unofficial mirror.
