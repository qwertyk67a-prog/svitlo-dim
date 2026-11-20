"""Manual Telegram onboarding. Never logs the bot token."""
import json
import os
import re
import sys
import time
import requests


class SetupError(RuntimeError):
    pass


def call_telegram(token, method, **params):
    # The token is part of the API URL; never print an exception containing it.
    try:
        response = requests.post(
            "https://api.telegram.org/bot" + token + "/" + method,
            data=params, timeout=18,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise SetupError("Telegram API недоступний") from None
    if not isinstance(payload, dict) or not payload.get("ok"):
        code = payload.get("error_code") if isinstance(payload, dict) else None
        safe = {
            401: "Неправильний TELEGRAM_BOT_TOKEN",
            403: "Боту заборонено доступ до чату",
            409: "Конфлікт: webhook або інший процес вже отримує оновлення бота",
            400: "Telegram відхилив запит. Перевірте чат і права бота",
        }
        raise SetupError(safe.get(code, "Помилка Telegram API"))
    return payload.get("result")


def recent_group_commands(updates, username, now=None):
    """Find opt-in /chatid commands from groups during the last 30 minutes."""
    now = time.time() if now is None else now
    pattern = re.compile(r"^/chatid(?:@([A-Za-z0-9_]{5,32}))?(?:\s|$)", re.I)
    found = set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        message = update.get("message") or update.get("edited_message") or {}
        if not isinstance(message, dict):
            continue
        chat = message.get("chat") or {}
        if not isinstance(chat, dict) or chat.get("type") not in ("group", "supergroup"):
            continue
        date = message.get("date")
        if not isinstance(date, (int, float)) or not now - 1800 <= date <= now + 60:
            continue
        match = pattern.match(str(message.get("text", "")))
        if not match:
            continue
        if match.group(1) and match.group(1).casefold() != username.casefold():
            continue
        chat_id = chat.get("id")
        if type(chat_id) is int and chat_id < 0:
            found.add(chat_id)
    return sorted(found)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SetupError("Потрібен Repository secret TELEGRAM_BOT_TOKEN")
    mode = os.getenv("SETUP_MODE", "discover")
    if mode not in ("discover", "test"):
        raise SetupError("Невідомий режим налаштування")
    me = call_telegram(token, "getMe")
    if not isinstance(me, dict) or not me.get("is_bot") or not me.get("username"):
        raise SetupError("Telegram не підтвердив бота")
    print("Бот підтверджений: @" + me["username"])
    if mode == "discover":
        updates = call_telegram(
            token, "getUpdates", limit=100, timeout=0,
            allowed_updates=json.dumps(["message", "edited_message"]),
        )
        groups = recent_group_commands(updates or [], me["username"])
        if not groups:
            raise SetupError("Команду /chatid не знайдено. Надішліть її в групу й повторіть discover")
        if len(groups) > 1:
            raise SetupError("Команда /chatid знайдена в кількох групах. Потрібне однозначне визначення")
        # Never print chat identifiers to public GitHub Actions logs.
        # The requesting group gets its own ID directly in Telegram.
        call_telegram(
            token, "sendMessage", chat_id=groups[0],
            disable_notification="true",
            text=("ID цієї групи: " + str(groups[0]) +
                  "\nЗбережіть ID у GitHub Secret TELEGRAM_CHAT_ID. "
                  "Не публікуйте токен бота."),
        )
        print("ID надіслано у групу Telegram; у GitHub-журналі його немає")
        return
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not re.fullmatch(r"-\d+", chat_id):
        raise SetupError("Потрібен від'ємний ID групи в секреті TELEGRAM_CHAT_ID")
    chat = call_telegram(token, "getChat", chat_id=chat_id)
    if not isinstance(chat, dict) or str(chat.get("id")) != chat_id:
        raise SetupError("Ідентифікатор групи не підтверджено")
    if chat.get("type") not in ("group", "supergroup"):
        raise SetupError("Чат не є Telegram-групою")
    call_telegram(
        token, "sendMessage", chat_id=chat_id, disable_notification="true",
        text=("✅ Тестове повідомлення бота відключень.\n"
              "З'єднання з Telegram працює. Моніторинг ще не активований."),
    )
    print("Одне тестове повідомлення надіслано. Моніторинг не активований.")


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print("Помилка налаштування: " + str(exc), file=sys.stderr)
        sys.exit(1)
