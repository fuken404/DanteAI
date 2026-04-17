import logging
import httpx
from telegram import Update
from telegram.ext import ContextTypes
from claude_client import get_response, extract_memories
from voice_handler import transcribe_voice
from web_handler import extract_urls, fetch_url_content
from database import (
    save_message, clear_history,
    save_memory, delete_memory,
    get_memories, get_message_count,
)

logger = logging.getLogger(__name__)

MEMORY_EXTRACT_EVERY = 10


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Hola {name}, soy *Dante*, tu asistente personal.\n\n"
        "Puedo ayudarte con:\n"
        "• Texto, notas de voz e imágenes\n"
        "• Vehiculos, deportes, finanzas y más\n\n"
        "Escribe /help para ver todos los comandos.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Comandos de Dante:*\n\n"
        "/start — Bienvenida\n"
        "/clear — Borrar historial del chat\n"
        "/memories — Ver lo que recuerdo de ti\n"
        "/remember clave: valor — Guardar un dato\n"
        "/forget clave — Olvidar un dato\n"
        "/help — Esta ayuda",
        parse_mode="Markdown",
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await clear_history(update.effective_user.id)
    await update.message.reply_text("Historial borrado. Empecemos de nuevo.")


async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memories = await get_memories(update.effective_user.id)
    if not memories:
        await update.message.reply_text("Aún no tengo información guardada sobre ti.")
        return
    lines = "\n".join(f"• *{k}*: {v}" for k, v in memories.items())
    await update.message.reply_text(f"*Lo que recuerdo de ti:*\n\n{lines}", parse_mode="Markdown")


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
    await update.message.reply_text(f"Guardado: *{key}* → {value}", parse_mode="Markdown")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.partition(" ")[2].strip().lower()
    if not key:
        await update.message.reply_text("Uso: /forget clave")
        return
    await delete_memory(update.effective_user.id, key)
    await update.message.reply_text(f"Olvidado: *{key}*", parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        urls = extract_urls(text)
        enriched_text = text
        if urls:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            contents = []
            for url in urls[:2]:  # max 2 URLs por mensaje
                content = await fetch_url_content(url)
                contents.append(f"[Contenido de {url}]\n{content}")
            enriched_text = text + "\n\n" + "\n\n".join(contents)

        await save_message(user.id, "user", text)
        reply = await get_response(user.id, user.first_name, enriched_text)
        await save_message(user.id, "assistant", reply)
        await update.message.reply_text(reply)

        count = await get_message_count(user.id)
        if count > 0 and count % MEMORY_EXTRACT_EVERY == 0:
            await extract_memories(user.id, user.first_name)

    except Exception as e:
        logger.error(f"Text handler error: {e}")
        await update.message.reply_text("Tuve un error. Intenta de nuevo.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        text = await transcribe_voice(voice_file.file_path)

        if not text or not text.strip():
            await update.message.reply_text("No pude entender el audio. ¿Puedes repetirlo?")
            return

        await update.message.reply_text(f"_{text}_", parse_mode="Markdown")

        await save_message(user.id, "user", text)
        reply = await get_response(user.id, user.first_name, text)
        await save_message(user.id, "assistant", reply)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await update.message.reply_text("No pude procesar el audio. Intenta de nuevo.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    caption = update.message.caption or "Analiza esta imagen y dime qué ves"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        photo_file = await context.bot.get_file(update.message.photo[-1].file_id)

        async with httpx.AsyncClient() as http:
            resp = await http.get(photo_file.file_path, timeout=30)
            image_data = resp.content

        await save_message(user.id, "user", f"[Imagen] {caption}")
        reply = await get_response(user.id, user.first_name, caption, image_data)
        await save_message(user.id, "assistant", reply)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        await update.message.reply_text("No pude procesar la imagen. Intenta de nuevo.")
