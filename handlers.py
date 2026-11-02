import html
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

import config
from claude_client import (
    BOGOTA, MAX_KEY_LENGTH, MAX_VALUE_LENGTH, ClaudeRefused, Profile,
    extract_memories, get_response, owner_profile, visitor_profile,
)
from database import (
    clear_history, clear_memories, consume_quota, count_memories, delete_memory,
    get_memories, get_message_count, save_exchange, save_memory,
)
from voice_handler import VoiceUnavailable, transcribe_voice
from web_handler import extract_urls, fetch_url_content, wrap_web_content

logger = logging.getLogger(__name__)

MEMORY_EXTRACT_EVERY = 10
MAX_URLS_PER_MESSAGE = 2
TELEGRAM_LIMIT = 4096

# Topes de los visitantes en la demo pública. Se revisan antes de gastar cupo.
VISITOR_MAX_TEXT = 1500
VISITOR_MAX_VOICE_SECONDS = 60
VISITOR_MAX_PHOTO_BYTES = 5 * 1024 * 1024
VISITOR_MAX_MEMORIES = 20
VISITOR_RETENTION_DAYS = 7

PUBLIC_NOTICE = (
    "\n\n<b>Esta es una demo pública:</b> tienes unos pocos mensajes al día. "
    f"Lo que me escribas se guarda hasta {VISITOR_RETENTION_DAYS} días y se procesa con Anthropic y Groq, "
    "así que no mandes datos sensibles. Lo borras cuando quieras con /clear todo.\n\n"
    "No soy un servicio de emergencias: en Colombia, llama al 123."
)


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


def is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return update.effective_user.id in context.bot_data["owners"]


async def admit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Profile | None:
    """Decide con qué perfil se responde. Sin cupo no se llama a ninguna API de pago."""
    if is_owner(update, context):
        return owner_profile()

    today = datetime.now(BOGOTA).date()
    result = await consume_quota(
        update.effective_user.id, today, config.daily_limit_per_user(), config.daily_limit_total(),
    )
    if result == "ok":
        return visitor_profile()
    if result == "user":
        await update.message.reply_text("Llegaste al límite de mensajes de la demo por hoy. Vuelve mañana.")
    else:
        await update.message.reply_text("La demo llegó a su límite por hoy. Vuelve mañana.")
    return None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = html.escape(update.effective_user.first_name)
    await update.message.reply_text(
        f"Hola {name}, soy <b>Dante</b>, tu asistente personal.\n\n"
        "Puedo ayudarte con:\n"
        "• Texto, notas de voz e imágenes\n"
        "• Vehículos, deportes, finanzas y más\n\n"
        "Escribe /help para ver todos los comandos."
        + ("" if is_owner(update, context) else PUBLIC_NOTICE),
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

    if not is_owner(update, context):
        key, value = key[:MAX_KEY_LENGTH], value[:MAX_VALUE_LENGTH]
        if await count_memories(update.effective_user.id) >= VISITOR_MAX_MEMORIES:
            await update.message.reply_text("Ya tienes muchos datos guardados. Borra alguno con /forget clave.")
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


async def answer(update: Update, profile: Profile, stored_text: str, prompt: str, image_data: bytes | None = None):
    """Pide la respuesta, la entrega, la guarda y de vez en cuando actualiza la memoria."""
    user = update.effective_user
    try:
        reply = await get_response(user.id, user.first_name, prompt, image_data, profile=profile)
    except ClaudeRefused:
        await update.message.reply_text("No puedo ayudarte con eso. Si quieres, intentemos con otra cosa.")
        return

    await save_exchange(user.id, stored_text, reply)
    for part in split_message(reply):
        await update.message.reply_text(part)

    if profile.auto_memory and await get_message_count(user.id) % MEMORY_EXTRACT_EVERY == 0:
        try:
            await extract_memories(user.id)
        except Exception as error:  # La memoria nunca debe tumbar una respuesta ya entregada.
            logger.warning("No se pudo actualizar la memoria: %s", type(error).__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not is_owner(update, context) and len(text) > VISITOR_MAX_TEXT:
        await update.message.reply_text(f"En la demo, escribe máximo {VISITOR_MAX_TEXT} caracteres por mensaje.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        profile = await admit(update, context)
        if profile is None:
            return
        prompt = text
        urls = extract_urls(text)[:MAX_URLS_PER_MESSAGE]
        for url in urls:
            prompt += "\n\n" + wrap_web_content(url, await fetch_url_content(url))
        await answer(update, profile, text, prompt)
    except Exception as error:
        logger.error("Error con un mensaje de texto: %s", type(error).__name__)
        await update.message.reply_text("Tuve un error. Intenta de nuevo.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update, context) and update.message.voice.duration > VISITOR_MAX_VOICE_SECONDS:
        await update.message.reply_text(f"En la demo, las notas de voz son de máximo {VISITOR_MAX_VOICE_SECONDS} segundos.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        profile = await admit(update, context)
        if profile is None:
            return
        voice_file = await update.message.voice.get_file()
        text = await transcribe_voice(bytes(await voice_file.download_as_bytearray()))

        if not text or not text.strip():
            await update.message.reply_text("No pude entender el audio. ¿Puedes repetirlo?")
            return

        for part in split_message(f"🎙 {text}"):
            await update.message.reply_text(part)
        await answer(update, profile, text, text)
    except VoiceUnavailable:
        await update.message.reply_text("Las notas de voz no están configuradas todavía.")
    except Exception as error:
        logger.error("Error con una nota de voz: %s", type(error).__name__)
        await update.message.reply_text("No pude procesar el audio. Intenta de nuevo.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.caption or "Analiza esta imagen y dime qué ves"
    if not is_owner(update, context) and (
        len(caption) > VISITOR_MAX_TEXT or (update.message.photo[-1].file_size or 0) > VISITOR_MAX_PHOTO_BYTES
    ):
        await update.message.reply_text("En la demo, esa imagen o su texto son demasiado grandes.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        profile = await admit(update, context)
        if profile is None:
            return
        photo_file = await update.message.photo[-1].get_file()
        image_data = bytes(await photo_file.download_as_bytearray())
        await answer(update, profile, f"[Imagen] {caption}", caption, image_data)
    except Exception as error:
        logger.error("Error con una imagen: %s", type(error).__name__)
        await update.message.reply_text("No pude procesar la imagen. Intenta de nuevo.")


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Último recurso: registra el tipo de error y avisa, sin mostrar detalles internos."""
    logger.error("Error sin manejar: %s", type(context.error).__name__)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("Tuve un error. Intenta de nuevo.")
