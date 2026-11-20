"""Inspect Telegram delivery metadata without printing chat content or tokens.

This is a one-time, read-only diagnostic. It never sends messages or stores
updates. The displayed group ID is the only public chat identifier emitted.
"""
import collections
import json
import os
import sys
from scripts.telegram_setup import call_telegram, recent_group_commands, SetupError


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SetupError("TELEGRAM_BOT_TOKEN відсутній у Repository secrets")
    me = call_telegram(token, "getMe")
    if not isinstance(me, dict) or not me.get("is_bot"):
        raise SetupError("Токен не належить Telegram-боту")
    username = me.get("username") or ""
    print("Telegram bot verified; username withheld from public logs")
    webhook = call_telegram(token, "getWebhookInfo")
    if not isinstance(webhook, dict):
        raise SetupError("Webhook status недоступний")
    print("Webhook configured:", bool(webhook.get("url")))
    print("Webhook pending updates:", int(webhook.get("pending_update_count") or 0))
    if webhook.get("url"):
        print("Webhook заважає getUpdates; нічого не видалено")
        return

    updates = call_telegram(
        token, "getUpdates", limit=100, timeout=0,
        allowed_updates=json.dumps([
            "message", "edited_message", "channel_post",
            "edited_channel_post", "my_chat_member",
        ]),
    )
    if not isinstance(updates, list):
        raise SetupError("Telegram повернув некоректний список оновлень")
    types = collections.Counter()
    chats = collections.Counter()
    for update in updates:
        if not isinstance(update, dict):
            continue
        for kind in ("message", "edited_message", "channel_post",
                     "edited_channel_post", "my_chat_member"):
            message = update.get(kind)
            if isinstance(message, dict):
                types[kind] += 1
                chat = message.get("chat") or {}
                if isinstance(chat, dict):
                    chats[str(chat.get("type", "unknown"))] += 1
    print("Unconfirmed updates:", len(updates))
    print("Update kinds:", dict(types))
    print("Chat kinds:", dict(chats))
    found = recent_group_commands(updates, username)
    print("Matching /chatid destinations:", len(found))
    if len(found) == 1:
        print("Знайдено рівно одну групу; ID не друкується у журналі")
        print("Запустіть Connect Telegram bot у режимі discover для отримання ID")
    elif not found:
        print("Групова команда не надійшла. Повторіть точну /chatid@ім'я_бота "
              "після діагностики та запустіть discover")
    else:
        print("Команду виявлено в кількох групах; потрібна одна цільова група")


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print("Діагностика: " + str(exc), file=sys.stderr)
        sys.exit(1)
