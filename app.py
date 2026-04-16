import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
PORT = int(os.getenv("PORT", "8080"))

WELCOME_TEXT = (
    "Привет! Отправь сюда своё резюме или информацию о себе.\n\n"
    "Можно прислать:\n"
    "• текстовое сообщение\n"
    "• PDF / DOCX / файл\n"
    "• фото\n\n"
    "Мы рассмотрим заявку и ответим здесь от имени бота."
)

def is_admin_chat(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID)

def build_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подходит", callback_data=f"accept:{user_id}"),
            InlineKeyboardButton("❌ Не подходит", callback_data=f"reject:{user_id}")
        ]
    ])

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/", "/health", "/healthz"]:
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"Health server started on port {PORT}")
    server.serve_forever()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(WELCOME_TEXT)

async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(f"Current chat ID: {update.effective_chat.id}")

async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Ошибка: user_id должен быть числом.")
        return

    text = " ".join(context.args[1:]).strip()

    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        await update.message.reply_text("Сообщение отправлено кандидату от имени бота.")
    except Exception as e:
        logger.exception("Failed to send custom reply")
        await update.message.reply_text(f"Не удалось отправить сообщение: {e}")

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
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"{header}\n\nТекст заявки:\n{msg.text}",
                reply_markup=build_keyboard(user.id)
            )
        else:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=header)
            await msg.forward(chat_id=ADMIN_CHAT_ID)
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"Решение по кандидату {user.full_name} (ID: {user.id})\n\n"
                    f"Для кастомного ответа:\n"
                    f"/reply {user.id} <ваш текст>"
                ),
                reply_markup=build_keyboard(user.id)
            )

        await msg.reply_text("Спасибо! Мы получили твою заявку и вернёмся с ответом.")
    except Exception as e:
        logger.exception("Failed to handle submission")
        await msg.reply_text("Произошла ошибка при отправке заявки. Попробуй ещё раз позже.")

async def handle_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        action, user_id_raw = query.data.split(":")
        user_id = int(user_id_raw)
    except Exception:
        if query.message:
            await query.message.reply_text("Ошибка обработки кнопки.")
        return

    if action == "accept":
        reply_text = (
            "Спасибо за отклик! Ты подходишь нам на следующий этап.\n\n"
            "Мы хотим пригласить тебя на короткое собеседование."
        )
        status_text = f"Кандидату {user_id} отправлен ответ: ПОДХОДИТ ✅"
    else:
        reply_text = (
            "Спасибо за отклик и за время, которое ты уделил заявке.\n\n"
            "Сейчас мы не готовы двигаться дальше, но благодарим за интерес к команде."
        )
        status_text = f"Кандидату {user_id} отправлен ответ: НЕ ПОДХОДИТ ❌"

    try:
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        if query.message:
            await query.message.reply_text(status_text)
    except Exception as e:
        logger.exception("Failed to send decision")
        if query.message:
            await query.message.reply_text(f"Не удалось отправить ответ кандидату: {e}")

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("reply", reply_cmd))
    app.add_handler(CallbackQueryHandler(handle_decision))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_submission))

    logger.info("Bot polling started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
