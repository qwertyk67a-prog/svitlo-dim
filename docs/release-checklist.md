# New installation and migration

1. Start with only the current main files in a new repository. Enable Actions. Give the installation its own operator settings, state key, Telegram destination and credentials in repository Secrets.
2. Run Initialize empty notification history for a new repository. It creates an orphan bot-state branch and refuses to replace existing history.
3. Run Verify configured address and the read-only source audit. Check the provider protocol, official assignment and daily schedule for the new house.
4. Verify the Telegram destination, enable the bot and inspect a production check.
5. Connect the dedicated Cloudflare Worker using the scheduler README. Confirm the private administrator message and subsequent GitHub dispatches.
6. Use the migration workflows only when preserving a legacy installation. Pause the legacy writer, preview the migration, verify encrypted state and resume delivery. Preserve the original encryption key for an existing installation.
