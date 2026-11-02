import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from claude_client import ClaudeRefused, extract_memories, get_response
from database import (
    clear_history, clear_memories, delete_memory,
    get_memories, get_message_count, save_exchange, save_memory,
)
from voice_handler import VoiceUnavailable, transcribe_voice
from web_handler import extract_urls, fetch_url_content, wrap_web_content

logger = logging.getLogger(__name__)

MEMORY_EXTRACT_EVERY = 10
MAX_URLS_PER_MESSAGE = 2
TELEGRAM_LIMIT = 4096


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Parte una respuesta larga para Telegram, en un salto de línea o espacio si se puede."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


async def reply_denied(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde sin llamar a ninguna API de pago. El ID sirve para configurar ALLOWED_USER_IDS."""
    await update.message.reply_text(f"Este bot es privado. Tu ID de Telegram es {update.effective_user.id}.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = html.escape(update.effective_user.first_name)
    await update.message.reply_text(
        f"Hola {name}, soy <b>Dante</b>, tu asistente personal.\n\n"
        "Puedo ayudarte con:\n"
        "• Texto, notas de voz e imágenes\n"
        "• Vehículos, deportes, finanzas y más\n\n"
        "Escribe /help para ver todos los comandos.",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Comandos de Dante:</b>\n\n"
        "/start — Bienvenida\n"
        "/clear — Borrar el historial del chat\n"
        "/clear todo — Borrar también lo que recuerdo de ti\n"
        "/memories — Ver lo que recuerdo de ti\n"
        "/remember clave: valor — Guardar un dato\n"
        "/forget clave — Olvidar un dato\n"
        "/help — Esta ayuda",
        parse_mode="HTML",
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await clear_history(user_id)
    if update.message.text.partition(" ")[2].strip().lower() == "todo":
        await clear_memories(user_id)
        await update.message.reply_text("Borré el historial y todo lo que recordaba de ti.")
        return
    await update.message.reply_text(
        "Historial borrado. Lo que recuerdo de ti sigue guardado; para borrarlo usa /clear todo."
    )


async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memories = await get_memories(update.effective_user.id)
    if not memories:
        await update.message.reply_text("Aún no tengo información guardada sobre ti.")
        return
    lines = "\n".join(f"• {k}: {v}" for k, v in memories.items())
    for part in split_message(f"Lo que recuerdo de ti:\n\n{lines}"):
        await update.message.reply_text(part)


async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = update.message.text.partition(" ")[2].strip()
    if ":" not in args:
        await update.message.reply_text("Uso: /remember clave: valor\nEjemplo: /remember vehículo: Toyota Hilux 2022")
        return

    key, _, value = args.partition(":")
    key, value = key.strip().lower(), value.strip()

    if not key or not value:
        await update.message.reply_text("Debes indicar clave y valor.")
        return

    await save_memory(update.effective_user.id, key, value)
    await update.message.reply_text(f"Guardado: <b>{html.escape(key)}</b> → {html.escape(value)}", parse_mode="HTML")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.partition(" ")[2].strip().lower()
    if not key:
        await update.message.reply_text("Uso: /forget clave")
        return
    if await delete_memory(update.effective_user.id, key):
        await update.message.reply_text(f"Olvidado: <b>{html.escape(key)}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("No tenía guardado ese dato.")


async def answer(update: Update, stored_text: str, prompt: str, image_data: bytes | None = None):
    """Pide la respuesta, la entrega, la guarda y de vez en cuando actualiza la memoria."""
    user = update.effective_user
    try:
        reply = await get_response(user.id, user.first_name, prompt, image_data)
    except ClaudeRefused:
        await update.message.reply_text("Parce, con eso no te puedo ayudar. Probemos con otra cosa.")
        return

    await save_exchange(user.id, stored_text, reply)
    for part in split_message(reply):
        await update.message.reply_text(part)

    if await get_message_count(user.id) % MEMORY_EXTRACT_EVERY == 0:
        try:
            await extract_memories(user.id)
        except Exception as error:  # La memoria nunca debe tumbar una respuesta ya entregada.
            logger.warning("No se pudo actualizar la memoria: %s", type(error).__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        prompt = text
        urls = extract_urls(text)[:MAX_URLS_PER_MESSAGE]
        for url in urls:
            prompt += "\n\n" + wrap_web_content(url, await fetch_url_content(url))
        await answer(update, text, prompt)
    except Exception as error:
        logger.error("Error con un mensaje de texto: %s", type(error).__name__)
        await update.message.reply_text("Tuve un error. Intenta de nuevo.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        voice_file = await update.message.voice.get_file()
        text = await transcribe_voice(bytes(await voice_file.download_as_bytearray()))

        if not text or not text.strip():
            await update.message.reply_text("No pude entender el audio. ¿Puedes repetirlo?")
            return

        for part in split_message(f"🎙 {text}"):
            await update.message.reply_text(part)
        await answer(update, text, text)
    except VoiceUnavailable:
        await update.message.reply_text("Las notas de voz no están configuradas todavía.")
    except Exception as error:
        logger.error("Error con una nota de voz: %s", type(error).__name__)
        await update.message.reply_text("No pude procesar el audio. Intenta de nuevo.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Analiza esta imagen y dime qué ves"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        photo_file = await update.message.photo[-1].get_file()
        image_data = bytes(await photo_file.download_as_bytearray())
        await answer(update, f"[Imagen] {caption}", caption, image_data)
    except Exception as error:
        logger.error("Error con una imagen: %s", type(error).__name__)
        await update.message.reply_text("No pude procesar la imagen. Intenta de nuevo.")


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Último recurso: registra el tipo de error y avisa, sin mostrar detalles internos."""
    logger.error("Error sin manejar: %s", type(context.error).__name__)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Tuve un error. Intenta de nuevo.")
