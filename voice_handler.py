import os

from groq import AsyncGroq


class VoiceUnavailable(Exception):
    """La transcripción no está configurada."""


async def transcribe_voice(audio: bytes) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise VoiceUnavailable()

    transcription = await AsyncGroq(api_key=api_key).audio.transcriptions.create(
        file=("audio.ogg", audio, "audio/ogg"),
        model="whisper-large-v3-turbo",
        language="es",
        response_format="text",
    )
    return transcription if isinstance(transcription, str) else transcription.text
