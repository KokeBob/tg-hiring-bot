import os
import json
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "1"))

WELCOME_TEXT = (
    "Привет! Отправь сюда своё резюме или информацию о себе.\n\n"
    "Можно прислать:\n"
    "• текстовое сообщение\n"
    "• PDF / DOCX / файл\n"
    "• фото\n\n"
    "Мы рассмотрим заявку и ответим здесь от имени бота."
)

HELP_TEXT = (
    "Команды для админов:\n\n"
    "/chatid — показать ID текущего чата\n"
    "/reply <текст> — ответить кандидату, если команда отправлена reply на карточку кандидата\n"
    "/reply <user_id> <текст> — ответить кандидату вручную по user_id\n\n"
    "Рекомендуемый способ:\n"
    "1. В админ-чате нажми Reply на сообщение по кандидату\n"
    "2. Напиши /reply <твой текст>"
)

DATA_FILE = Path("state.json")


def load_state() -> dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Failed to load state.json")
    return {
        "admin_message_to_user": {},   # admin_message_id -> user_id
        "decisions": {}                # user_id -> accepted/rejected/custom
    }


def save_state(state: dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Failed to save state.json")


STATE = load_state()


def is_admin_chat(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID)


def keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подходит", callback_data=f"accept:{user_id}"),
            InlineKeyboardButton("❌ Не подходит", callback_data=f"reject:{user_id}")
        ]
    ])


def set_mapping(admin_message_id: int, user_id: int) -> None:
    STATE["admin_message_to_user"][str(admin_message_id)] = user_id
    save_state(STATE)


def get_user_id_from_admin_message(message_id: int):
    return STATE["admin_message_to_user"].get(str(message_id))


def has_decision(user_id: int) -> bool:
    return str(user_id) in STATE["decisions"]


def mark_decision(user_id: int, decision: str) -> None:
    STATE["decisions"][str(user_id)] = decision
    save_state(STATE)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(WELCOME_TEXT)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_chat(update):
        return
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(f"Current chat ID: {update.effective_chat.id}")


async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_chat(update):
        return
    if not update.message:
        return

    # Вариант 1: reply на сообщение о кандидате
    if update.message.reply_to_message and context.args:
        replied_message_id = update.message.reply_to_message.message_id
        user_id = get_user_id_from_admin_message(replied_message_id)

        if user_id:
            text = " ".join(context.args).strip()
            try:
                await context.bot.send_message(chat_id=user_id, text=text)
                mark_decision(user_id, "custom")
                await update.message.reply_text("Кастомный ответ отправлен кандидату.")
            except Exception as e:
                logger.exception("Failed to send reply via replied message")
                await update.message.reply_text(f"Не удалось отправить сообщение: {e}")
            return

    # Вариант 2: /reply <user_id> <текст>
    if len(context.args) >= 2:
        try:
            user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(
                "Неверный формат.\n\n"
                "Либо ответь реплаем на карточку кандидата:\n"
                "/reply Спасибо! Хотим позвать тебя на интервью.\n\n"
                "Либо используй:\n"
                "/reply <user_id> <текст>"
            )
            return

        text = " ".join(context.args[1:]).strip()

        try:
            await context.bot.send_message(chat_id=user_id, text=text)
            mark_decision(user_id, "custom")
            await update.message.reply_text("Кастомный ответ отправлен кандидату.")
        except Exception as e:
            logger.exception("Failed to send manual reply")
            await update.message.reply_text(f"Не удалось отправить сообщение: {e}")
        return

    await update.message.reply_text(
        "Использование:\n\n"
        "1. Ответь реплаем на карточку кандидата:\n"
        "/reply Спасибо! Хотим позвать тебя на интервью.\n\n"
        "2. Или вручную:\n"
        "/reply <user_id> <текст>"
    )


async def handle_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user or not chat:
        return

    if chat.type != "private":
        return

    header = (
        "Новая заявка\n\n"
        f"Имя: {user.full_name}\n"
        f"Username: @{user.username if user.username else 'нет'}\n"
        f"User ID: {user.id}"
    )

    try:
        if msg.text:
            admin_msg = await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"{header}\n\n"
                    f"Текст заявки:\n{msg.text}\n\n"
                    f"Чтобы ответить вручную, нажми Reply на это сообщение и напиши:\n"
                    f"/reply <твой текст>"
                ),
                reply_markup=keyboard(user.id)
            )
            set_mapping(admin_msg.message_id, user.id)

        else:
            header_msg = await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=header
            )
            set_mapping(header_msg.message_id, user.id)

            forwarded_msg = await msg.forward(chat_id=ADMIN_CHAT_ID)
            set_mapping(forwarded_msg.message_id, user.id)

            decision_msg = await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"Решение по кандидату {user.full_name} (ID: {user.id})\n\n"
                    f"Чтобы ответить вручную, нажми Reply на это сообщение и напиши:\n"
                    f"/reply <твой текст>"
                ),
                reply_markup=keyboard(user.id)
            )
            set_mapping(decision_msg.message_id, user.id)

        await msg.reply_text("Спасибо! Мы получили твою заявку и вернёмся с ответом.")
    except Exception:
        logger.exception("Failed to process candidate submission")
        await msg.reply_text("Произошла ошибка при отправке заявки. Попробуй ещё раз позже.")


async def handle_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message:
        return

    await query.answer()

    try:
        action, user_id_raw = query.data.split(":")
        user_id = int(user_id_raw)
    except Exception:
        await query.message.reply_text("Ошибка обработки кнопки.")
        return

    # Уже приняли решение ранее
    if has_decision(user_id):
        await query.answer("Решение уже принято", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if action == "accept":
        text = (
            "Спасибо за отклик! Ты подходишь нам на следующий этап.\n\n"
            "Мы хотим пригласить тебя на короткое собеседование."
        )
        status = "Статус: ПОДХОДИТ ✅"
        decision_value = "accepted"
    else:
        text = (
            "Спасибо за отклик и за время, которое ты уделил заявке.\n\n"
            "Сейчас мы не готовы двигаться дальше, но благодарим за интерес к команде."
        )
        status = "Статус: НЕ ПОДХОДИТ ❌"
        decision_value = "rejected"

    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        mark_decision(user_id, decision_value)

        # убираем кнопки
        await query.edit_message_reply_markup(reply_markup=None)

        # дописываем статус
        try:
            old_text = query.message.text or ""
            new_text = f"{old_text}\n\n{status}"
            if len(new_text) <= 4096:
                await query.edit_message_text(new_text)
            else:
                await query.message.reply_text(status)
        except Exception:
            await query.message.reply_text(status)

    except Exception as e:
        logger.exception("Failed to send decision")
        await query.message.reply_text(f"Не удалось отправить ответ кандидату: {e}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("reply", reply_cmd))
    app.add_handler(CallbackQueryHandler(handle_decision))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_submission))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
