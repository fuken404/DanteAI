import os
import tempfile
import httpx

GROQ_API_KEY = os.getenv("GROQ_API_KEY")


async def transcribe_voice(file_url: str) -> str:
    if not GROQ_API_KEY:
        return "[Transcripción no disponible: configura GROQ_API_KEY]"

    from groq import AsyncGroq

    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

    async with httpx.AsyncClient() as http:
        response = await http.get(file_url, timeout=30)
        audio_data = response.content

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(audio_data)
        temp_path = f.name

    try:
        with open(temp_path, "rb") as audio_file:
            transcription = await groq_client.audio.transcriptions.create(
                file=("audio.ogg", audio_file, "audio/ogg"),
                model="whisper-large-v3-turbo",
                language="es",
                response_format="text",
            )
        return transcription if isinstance(transcription, str) else transcription.text
    finally:
        os.unlink(temp_path)
