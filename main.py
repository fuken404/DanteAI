import config  # noqa: F401  Debe ir primero: carga .env antes que los demás módulos.

import asyncio
import hmac
import logging
import os
import signal

from aiohttp import web
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from database import close_pool, init_db, purge_visitor_data
from handlers import (
    VISITOR_RETENTION_DAYS, clear_command, forget_command, handle_error, handle_photo,
    handle_text, handle_voice, help_command,
    memories_command, remember_command, reply_denied, start_command,
)

logger = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message"]
PURGE_EVERY_SECONDS = 6 * 60 * 60
CONCURRENT_UPDATES = 8  # un visitante lento no debe frenar a los demás
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
PTB_APP = web.AppKey("ptb_app", Application)
WEBHOOK_SECRET = web.AppKey("webhook_secret", str)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="Dante activo")


async def webhook_handler(request: web.Request) -> web.Response:
    if not hmac.compare_digest(request.headers.get(SECRET_HEADER, ""), request.app[WEBHOOK_SECRET]):
        return web.Response(status=401)

    ptb_app = request.app[PTB_APP]
    try:
        update = Update.de_json(await request.json(), ptb_app.bot)
    except ValueError:
        return web.Response(status=400)
    # Se responde de inmediato: Claude puede tardar y Telegram reintentaría el mensaje.
    await ptb_app.update_queue.put(update)
    return web.Response(text="OK")


def create_web_app(ptb_app: Application, secret: str) -> web.Application:
    web_app = web.Application(client_max_size=1024 * 1024)
    web_app[PTB_APP] = ptb_app
    web_app[WEBHOOK_SECRET] = secret
    web_app.router.add_get("/", health)
    web_app.router.add_post("/webhook", webhook_handler)
    return web_app


def build_app(token: str, allowed_ids: set[int], public: bool = False) -> Application:
    app = Application.builder().token(token).concurrent_updates(CONCURRENT_UPDATES).build()
    app.bot_data["owners"] = allowed_ids
    # Con allow_empty=False, una lista vacía no deja pasar a nadie.
    owner = filters.User(user_id=allowed_ids, allow_empty=False) & filters.ChatType.PRIVATE
    # En modo público, los visitantes pasan y cada mensaje descuenta de su cupo.
    admitted = filters.ChatType.PRIVATE if public else owner

    app.add_handler(CommandHandler("start", start_command, filters=admitted))
    app.add_handler(CommandHandler("help", help_command, filters=admitted))
    app.add_handler(CommandHandler("clear", clear_command, filters=admitted))
    app.add_handler(CommandHandler("memories", memories_command, filters=admitted))
    app.add_handler(CommandHandler("remember", remember_command, filters=admitted))
    app.add_handler(CommandHandler("forget", forget_command, filters=admitted))
    app.add_handler(MessageHandler(admitted & filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(admitted & filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(admitted & filters.PHOTO, handle_photo))
    if not public:
        app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~owner, reply_denied))
    app.add_error_handler(handle_error)
    return app


async def purge_periodically(owner_ids: set[int]) -> None:
    """También corre al arrancar: en un plan gratuito el servidor se duerme entre mensajes."""
    while True:
        try:
            await purge_visitor_data(owner_ids, VISITOR_RETENTION_DAYS)
        except Exception as error:
            logger.warning("No se pudieron borrar datos vencidos: %s", type(error).__name__)
        await asyncio.sleep(PURGE_EVERY_SECONDS)


async def wait_for_stop_signal() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()


async def run_webhook(ptb_app: Application, base_url: str, secret: str):
    webhook_url = f"{base_url}/webhook"
    await ptb_app.bot.set_webhook(webhook_url, secret_token=secret, allowed_updates=ALLOWED_UPDATES)
    logger.info("Webhook registrado: %s", webhook_url)

    runner = web.AppRunner(create_web_app(ptb_app, secret))
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Servidor escuchando en puerto %s", port)

    await wait_for_stop_signal()
    await runner.cleanup()


async def run_polling(ptb_app: Application):
    await ptb_app.bot.delete_webhook()
    await ptb_app.updater.start_polling(drop_pending_updates=True, allowed_updates=ALLOWED_UPDATES)
    logger.info("Polling iniciado")

    await wait_for_stop_signal()
    await ptb_app.updater.stop()


async def main():
    config.setup_logging()

    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN no configurado")

    base_url = config.webhook_base_url()
    secret = os.getenv("WEBHOOK_SECRET", "")
    if base_url and not secret:
        raise RuntimeError("WEBHOOK_SECRET es obligatorio en modo webhook")

    allowed_ids = config.allowed_user_ids()
    public = config.public_demo_enabled()
    if not allowed_ids and not public:
        logger.warning("ALLOWED_USER_IDS está vacío: nadie podrá usar el bot")
    logger.info("Demo pública: %s", "activa" if public else "apagada")

    await init_db()
    purge_task = asyncio.create_task(purge_periodically(allowed_ids))

    ptb_app = build_app(token, allowed_ids, public)
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
    logger.info("Dante listo como @%s", me.username)
    await ptb_app.start()

    try:
        if base_url:
            await run_webhook(ptb_app, base_url, secret)
        else:
            await run_polling(ptb_app)
    finally:
        purge_task.cancel()
        await ptb_app.stop()
        await ptb_app.shutdown()
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
