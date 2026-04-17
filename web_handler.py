import re
import httpx
from bs4 import BeautifulSoup

URL_PATTERN = re.compile(r'https?://[^\s]+')


def extract_urls(text: str) -> list[str]:
    return URL_PATTERN.findall(text)


async def fetch_url_content(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        for tag in soup(["script", "style", "nav", "footer", "aside", "iframe"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else ""
        body = soup.get_text(separator="\n", strip=True)

        # Trim to avoid exceeding context limits
        if len(body) > 6000:
            body = body[:6000] + "\n[... contenido recortado ...]"

        return f"Título: {title}\n\n{body}"

    except httpx.HTTPStatusError as e:
        return f"[Error al abrir la URL: HTTP {e.response.status_code}]"
    except Exception as e:
        return f"[No se pudo acceder a la URL: {e}]"
