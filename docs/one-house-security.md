# Security boundary for one-house SVITLO-DIM

The repository is public. Only `POWER_CONFIG_JSON`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, and GitHub-issued runtime tokens belong in protected
Secrets. Never publish their values in logs, comments, commits, or screenshots.

The active `main` branch **still has old public notification-state commits**.
The proposed feature version writes future `bot-state` checkpoints with
AES-256-GCM authenticated encryption. The encryption key is derived from
`POWER_CONFIG_JSON.state_key`. Key rotation without re-encrypting old state
makes the checkpoint unreadable. Encryption does not retroactively remove
historic commits. Plaintext exists transiently on an isolated Actions runner.

Do not run the live Telegram notification workflow from a feature branch.
The feature workflow gate rejects all refs except `refs/heads/main`.
Read-only source tests do not use Telegram Secrets.

The read-only health workflow checks recent GitHub monitor success but
shares GitHub infrastructure. It cannot detect a complete GitHub outage
independently and does not send Telegram administration alerts.

Repository workflow editors with access to Actions secrets remain trusted.
Compromised trusted code can still read those secrets at runtime. This
cannot be mitigated by checkpoint encryption alone.

No paid data storage, geocoding, or monitoring service is required.
