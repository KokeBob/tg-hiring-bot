import os
import asyncio
import logging
from typing import Optional

from fastapi import FastAPI
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not ADMIN_CHAT_ID:
    raise RuntimeError("ADMIN_CHAT_ID is not set")

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

WELCOME_TEXT = (
    "Привет! Отправь сюда своё резюме или информацию о себе.\n\n"
    "Можно прислать:\n"
    "• текстовое сообщение\n"
    "• PDF / DOCX / файл\n"
    "• фото\n\n"
    "Мы рассмотрим заявку и ответим здесь от имени бота."
)

HELP_TEXT = (
    "Команды для админов:\n"
    "/chatid — показать id текущего чата\n"
    "/reply <user_id> <текст> — отправить кандидату анонимный ответ\n\n"
    "Пример:\n"
    "/reply 123456789 Спасибо! Хотим пригласить тебя на интервью."
)

app = FastAPI()
telegram_app: Optional[Application] = None


def is_admin_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.id == ADMIN_CHAT_ID)


def build_decision_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Подходит", callback_data=f"accept:{user_id}"),
                InlineKeyboardButton("❌ Не подходит", callback_data=f"reject:{user_id}"),
            ]
        ]
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(WELCOME_TEXT)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin_chat(update):
        return
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(f"Current chat ID: `{update.effective_chat.id}`", parse_mode="Markdown")


async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin_chat(update):
        return

    if not update.message:
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Использование:\n/reply <user_id> <текст>\n\n"
            "Пример:\n/reply 123456789 Спасибо! Хотим пригласить тебя на интервью."
        )
        return

    user_id_raw = context.args[0]
    reply_text = " ".join(context.args[1:]).strip()

    try:
        user_id = int(user_id_raw)
    except ValueError:
        await update.message.reply_text("Ошибка: user_id должен быть числом.")
        return

    try:
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await update.message.reply_text("Сообщение отправлено кандидату от имени бота.")
    except Exception as e:
        logger.exception("Failed to send admin reply")
        await update.message.reply_text(f"Не удалось отправить сообщение: {e}")


async def handle_candidate_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает заявки только из личных сообщений пользователей.
    """
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return

    if chat.type != ChatType.PRIVATE:
        return

    header = (
        "Новая заявка\n\n"
        f"Имя: {user.full_name}\n"
        f"Username: @{user.username if user.username else 'нет'}\n"
        f"User ID: {user.id}"
    )

    keyboard = build_decision_keyboard(user.id)

    try:
        if message.text:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"{header}\n\nТекст заявки:\n{message.text}",
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=header)
            await message.forward(chat_id=ADMIN_CHAT_ID)
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"Решение по кандидату {user.full_name} (ID: {user.id})\n\n"
                    f"Для кастомного ответа:\n"
                    f"/reply {user.id} <ваш текст>"
                ),
                reply_markup=keyboard,
            )

        await message.reply_text("Спасибо! Мы получили твою заявку и вернёмся с ответом.")
    except Exception as e:
        logger.exception("Failed to process candidate submission")
        await message.reply_text("Произошла ошибка при отправке заявки. Попробуй ещё раз чуть позже.")


async def handle_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    if query.message and query.message.chat_id != ADMIN_CHAT_ID:
        await query.answer("Недоступно", show_alert=True)
        return

    await query.answer()

    try:
        action, user_id_raw = query.data.split(":")
        user_id = int(user_id_raw)
    except Exception:
        await query.message.reply_text("Ошибка обработки кнопки.")
        return

    if action == "accept":
        reply_text = (
            "Спасибо за отклик! Ты подходишь нам на следующий этап.\n\n"
            "Мы хотим пригласить тебя на короткое собеседование."
        )
        status_text = f"Кандидату {user_id} отправлен ответ: ПОДХОДИТ ✅"
    elif action == "reject":
        reply_text = (
            "Спасибо за отклик и за время, которое ты уделил заявке.\n\n"
            "Сейчас мы не готовы двигаться дальше, но благодарим за интерес к команде."
        )
        status_text = f"Кандидату {user_id} отправлен ответ: НЕ ПОДХОДИТ ❌"
    else:
        await query.message.reply_text("Неизвестное действие.")
        return

    try:
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await query.message.reply_text(status_text)
    except Exception as e:
        logger.exception("Failed to send decision")
        await query.message.reply_text(f"Не удалось отправить ответ кандидату: {e}")


async def on_startup() -> None:
    global telegram_app

    telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(CommandHandler("help", help_cmd))
    telegram_app.add_handler(CommandHandler("chatid", chatid_cmd))
    telegram_app.add_handler(CommandHandler("reply", reply_cmd))
    telegram_app.add_handler(CallbackQueryHandler(handle_decision))
    telegram_app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_candidate_submission)
    )

    await telegram_app.initialize()
    await telegram_app.start()

    # long polling
    await telegram_app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

    logger.info("Telegram bot started")


async def on_shutdown() -> None:
    global telegram_app

    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        logger.info("Telegram bot stopped")


@app.on_event("startup")
async def fastapi_startup() -> None:
    asyncio.create_task(on_startup())


@app.on_event("shutdown")
async def fastapi_shutdown() -> None:
    await on_shutdown()


@app.get("/")
async def root():
    return {"ok": True, "service": "tg-hiring-bot"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
