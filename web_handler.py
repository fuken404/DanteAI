import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 6000
READABLE_TYPES = ("text/", "application/xhtml")
HEADERS = {"User-Agent": "DanteBot/2.0 (asistente personal; +https://github.com/fuken404/DanteAI)"}


class BlockedUrl(Exception):
    """La URL apunta a una dirección que el bot no debe abrir."""


def extract_urls(text: str) -> list[str]:
    return [url.rstrip(".,;:!?)]}") for url in URL_PATTERN.findall(text)]


async def check_public_url(url: str) -> None:
    """Rechaza lo que no resuelva a direcciones públicas, como la red interna del servidor."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BlockedUrl()
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise BlockedUrl() from error
    if not infos:
        raise BlockedUrl()
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        mapped = getattr(address, "ipv4_mapped", None)
        if not (mapped or address).is_global:
            raise BlockedUrl()


async def download(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """Descarga con límite de tamaño y revisa cada redirección. Devuelve el texto y su tipo."""
    for _ in range(MAX_REDIRECTS + 1):
        await check_public_url(url)
        async with client.stream("GET", url, headers=HEADERS) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith(READABLE_TYPES):
                return "", content_type

            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= MAX_DOWNLOAD_BYTES:
                    break
            encoding = response.charset_encoding or "utf-8"
            return bytes(body[:MAX_DOWNLOAD_BYTES]).decode(encoding, errors="replace"), content_type
    raise httpx.TooManyRedirects("Demasiadas redirecciones")


def read_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "aside", "iframe"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    body = soup.get_text(separator="\n", strip=True)

    if len(body) > MAX_TEXT_CHARS:
        body = body[:MAX_TEXT_CHARS] + "\n[... contenido recortado ...]"

    return f"Título: {title}\n\n{body}"


async def fetch_url_content(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=15) as client:
            html, content_type = await download(client, url)
    except BlockedUrl:
        return "[No se abrió la URL: no es una dirección pública]"
    except httpx.HTTPStatusError as error:
        return f"[Error al abrir la URL: HTTP {error.response.status_code}]"
    except (httpx.HTTPError, ValueError, LookupError):
        return "[No se pudo acceder a la URL]"

    if not html:
        return f"[La URL no es una página de texto: {content_type or 'tipo desconocido'}]"
    return read_html(html)


def wrap_web_content(url: str, content: str) -> str:
    """Marca el contenido externo para que Claude lo trate como información y no como órdenes."""
    safe = content.replace("</contenido_web>", "")
    return f'<contenido_web url="{url}">\n{safe}\n</contenido_web>'
