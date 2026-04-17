import anthropic
import base64
import json
import os
from datetime import date
from database import get_history, get_memories, save_memory

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = """Eres Dante, el asistente personal de IA de {name}. Eres inteligente, directo y muy útil.

Tu personalidad:
- Hablas en español de manera natural y cercana, como un amigo de confianza
- Eres experto en vehículos (mecánica, compra/venta, mantenimiento), deportes, finanzas personales e inversiones
- Eres práctico y concreto: das información útil y accionable, sin rodeos
- Recuerdas el contexto y las preferencias del usuario entre conversaciones
- Cuando analizas imágenes, eres detallado y preciso

Lo que sabes sobre {name}:
{memories}

Fecha de hoy: {today}"""


async def get_response(
    user_id: int,
    user_name: str,
    message: str,
    image_data: bytes = None,
    image_mime: str = "image/jpeg",
) -> str:
    history = await get_history(user_id)
    memories = await get_memories(user_id)

    memories_text = (
        "\n".join(f"- {k}: {v}" for k, v in memories.items())
        if memories
        else "Aún no tengo información guardada sobre ti."
    )

    system = SYSTEM_PROMPT.format(
        name=user_name,
        memories=memories_text,
        today=date.today().strftime("%d/%m/%Y"),
    )

    messages = [{"role": r["role"], "content": r["content"]} for r in history]

    if image_data:
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_mime,
                    "data": base64.standard_b64encode(image_data).decode(),
                },
            },
            {"type": "text", "text": message or "¿Qué ves en esta imagen?"},
        ]
    else:
        content = message

    messages.append({"role": "user", "content": content})

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
    )

    return response.content[0].text


async def extract_memories(user_id: int, user_name: str):
    """Extract key facts from recent conversation and persist them."""
    history = await get_history(user_id, limit=10)
    if len(history) < 4:
        return

    messages = [{"role": r["role"], "content": r["content"]} for r in history]
    messages.append({
        "role": "user",
        "content": (
            "Analiza esta conversación y extrae datos clave del usuario en JSON. "
            "Solo incluye info duradera: nombre, vehículo, trabajo, ciudad, intereses, preferencias financieras. "
            "Responde SOLO con JSON válido, sin texto adicional. "
            'Ejemplo: {"vehiculo": "Toyota Corolla 2019", "ciudad": "Bogotá", "trabajo": "ingeniero"}'
        ),
    })

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=messages,
        )
        raw = response.content[0].text.strip()
        data = json.loads(raw)
        for key, value in data.items():
            if value and str(value).strip():
                await save_memory(user_id, key.lower(), str(value))
    except Exception:
        pass
