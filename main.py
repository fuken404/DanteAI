import logging
import os
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from handlers import (
    start_command, help_command, clear_command,
    memories_command, remember_command, forget_command,
    handle_text, handle_voice, handle_photo,
)
from database import init_db
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    await init_db()
    await application.bot.set_my_commands([
        BotCommand("start", "Bienvenida"),
        BotCommand("help", "Ver comandos"),
        BotCommand("clear", "Borrar historial"),
        BotCommand("memories", "Ver mis datos guardados"),
        BotCommand("remember", "Guardar un dato"),
        BotCommand("forget", "Olvidar un dato"),
    ])
    me = await application.bot.get_me()
    logger.info(f"Dante listo como @{me.username}")


def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN no configurado")

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("memories", memories_command))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
