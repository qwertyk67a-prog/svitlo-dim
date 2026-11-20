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

No fixed queue is stored in the production code. The address API returns
one or several verified queues. Changes are announced relative to the last
confirmed set, never to a guessed default. Uncertain address data do not
trigger new queue assignments or false power restoration.

## State and security

The public bot_state.json checkpoint contains queue identifiers, timetable
hashes and recent event status. It is not encrypted. A secret-key HMAC
namespace prevents a clone from reusing a different property's checkpoints.
It does not encrypt historical commits or current queue identifiers.
No paid hosting or encryption service has been added.

GitHub Actions scheduling is best-effort; exact five-minute updates and
exactly-once Telegram delivery are not guaranteed.
