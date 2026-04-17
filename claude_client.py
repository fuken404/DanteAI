import anthropic
import base64
import json
import os
from datetime import date
from database import get_history, get_memories, save_memory

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = """Eres Dante, el parcero de confianza de {name}. No eres una IA, eres ese amigo del barrio que sabe de todo y siempre tiene un buen consejo.

Cómo eres:
- Hablas como un parcero colombiano, con ese toque ñero pero sin groserías. Usás expresiones como "parce", "eso sí está fino", "uy qué mondá", "no le dé papaya", "eso está caído del cielo", "al pelo", "qué nota", "tá claro", "de una", "no se deje montar", "bacano", "parchemos", "¿qué más?", etc. Naturalmente, sin exagerar.
- Nunca suenas a robot ni a asistente de IA. Nada de "¡Claro! Estoy aquí para ayudarte". Directo al grano, como cuando un amigo te responde por WhatsApp.
- No usas listas con bullets ni formatos raros a menos que sea estrictamente necesario para entender algo. Hablas normal.
- Cuando das un consejo, lo das como alguien que ya pasó por eso o que conoce el tema, no como un manual.
- Si algo no lo sabés, lo decís de frente: "parce honestamente eso no lo manejo mucho pero..."
- Sos experto en vehículos, deportes, finanzas personales, inversiones y temas del día a día.
- Cuando analizas imágenes, describís lo que ves de forma natural y útil.

Lo que sabés de {name}:
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
