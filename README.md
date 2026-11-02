# DanteAI

Asistente personal en Telegram con personalidad de parcero colombiano y memoria persistente. Conversa por texto, notas de voz e imágenes, y lee los enlaces que le envían.

Es un bot privado: solo responde a los usuarios autorizados, para que nadie más gaste los créditos de las APIs.

## Qué hace

- **Texto e imágenes** con la API de Claude.
- **Notas de voz** transcritas con Whisper en Groq.
- **Memoria:** guarda la conversación en PostgreSQL y extrae datos duraderos con salidas estructuradas. Se revisan con `/memories` y se borran con `/forget` o `/clear todo`.
- **Enlaces:** lee hasta dos por mensaje y trata su contenido como información, no como instrucciones.

## Seguridad

| Riesgo | Defensa |
|---|---|
| Que otros gasten los créditos | Lista `ALLOWED_USER_IDS`; si está vacía no entra nadie, y un extraño no genera llamadas de pago |
| Mensajes falsos al webhook | Exige el secreto de Telegram, y sin `WEBHOOK_SECRET` el bot no arranca en ese modo |
| Abrir la red interna con un enlace | Solo direcciones públicas, revisando cada redirección y limitando la descarga a 2 MB |
| Filtrar el token en los logs | La URL de Telegram lo lleva dentro: las librerías HTTP no registran URLs y los secretos se reemplazan en cada línea |

Límite conocido: la dirección de un enlace se revisa antes de conectarse, así que un dominio que cambie de IP justo en ese instante podría evitar la revisión.

## Correr en local

Necesita Python 3.12, PostgreSQL y un bot creado en [@BotFather](https://t.me/BotFather).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # completa los valores
python main.py
```

Sin `WEBHOOK_URL` ni `RENDER_EXTERNAL_HOSTNAME` usa polling. Para saber tu ID, escríbele al bot: te lo dirá en la respuesta de acceso denegado.

## Desplegar

Corre en cualquier servicio con Python o Docker. Define las variables de `.env.example`, incluidas `WEBHOOK_URL` y `WEBHOOK_SECRET`. En Render, `RENDER_EXTERNAL_HOSTNAME` ya viene definida.

## Pruebas

No gastan tokens: usan el SDK real de Claude con un transporte simulado. Las de base de datos se activan con una PostgreSQL de pruebas, cuyos datos del usuario de prueba se borran.

```bash
TEST_DATABASE_URL=postgresql://usuario:clave@localhost/pruebas pytest
```

## Stack

Python 3.12 · python-telegram-bot · aiohttp · Claude API · Groq · PostgreSQL · Docker · GitHub Actions
