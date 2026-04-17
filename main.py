import asyncio
import logging
import os
import signal

from aiohttp import web
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from database import init_db
from handlers import (
    clear_command, forget_command, handle_photo,
    handle_text, handle_voice, help_command,
    memories_command, remember_command, start_command,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="Dante activo")


async def webhook_handler(request: web.Request) -> web.Response:
    ptb_app = request.app["ptb_app"]
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return web.Response(text="OK")


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("memories", memories_command))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    return app


async def run_webhook(ptb_app: Application):
    render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    webhook_url = f"https://{render_hostname}/webhook"

    await ptb_app.bot.set_webhook(webhook_url)
    logger.info(f"Webhook registrado: {webhook_url}")

    web_app = web.Application()
    web_app["ptb_app"] = ptb_app
    web_app.router.add_get("/", health)
    web_app.router.add_post("/webhook", webhook_handler)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"Servidor escuchando en puerto {port}")

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    await runner.cleanup()


async def run_polling(ptb_app: Application):
    await ptb_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Polling iniciado")

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    await ptb_app.updater.stop()


async def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN no configurado")

    await init_db()

    ptb_app = build_app(token)
    await ptb_app.initialize()
    await ptb_app.bot.set_my_commands([
        BotCommand("start", "Bienvenida"),
        BotCommand("help", "Ver comandos"),
        BotCommand("clear", "Borrar historial"),
        BotCommand("memories", "Ver mis datos guardados"),
        BotCommand("remember", "Guardar un dato"),
        BotCommand("forget", "Olvidar un dato"),
    ])
    me = await ptb_app.bot.get_me()
    logger.info(f"Dante listo como @{me.username}")
    await ptb_app.start()

    if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
        await run_webhook(ptb_app)
    else:
        await run_polling(ptb_app)

    await ptb_app.stop()
    await ptb_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
