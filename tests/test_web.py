import httpx
import pytest

import web_handler
from web_handler import BlockedUrl, check_public_url, download, extract_urls, fetch_url_content, read_html


def test_extraer_urls_sin_puntuacion():
    assert extract_urls("mira https://a.co/x. y (http://b.co/y)") == ["https://a.co/x", "http://b.co/y"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "http://192.168.1.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "file:///etc/passwd", "ftp://example.com/", "http:///sin-host",
])
async def test_bloquea_direcciones_internas(url):
    with pytest.raises(BlockedUrl):
        await check_public_url(url)


async def test_permite_direccion_publica():
    await check_public_url("http://8.8.8.8/")


async def public_ok(url):
    return None


def client_with(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def test_redireccion_a_red_interna_se_bloquea():
    async with client_with(lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"})) as client:
        with pytest.raises(BlockedUrl):
            await download(client, "http://8.8.8.8/")


async def test_limite_de_redirecciones(monkeypatch):
    monkeypatch.setattr(web_handler, "check_public_url", public_ok)
    async with client_with(lambda r: httpx.Response(302, headers={"location": "/otra"})) as client:
        with pytest.raises(httpx.TooManyRedirects):
            await download(client, "http://a.co/")


async def test_limite_de_descarga(monkeypatch):
    monkeypatch.setattr(web_handler, "check_public_url", public_ok)
    big = b"a" * (web_handler.MAX_DOWNLOAD_BYTES * 2)
    async with client_with(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=big)) as client:
        text, _ = await download(client, "http://a.co/")
    assert len(text) == web_handler.MAX_DOWNLOAD_BYTES


async def test_no_lee_binarios(monkeypatch):
    monkeypatch.setattr(web_handler, "check_public_url", public_ok)
    async with client_with(lambda r: httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK")) as client:
        text, content_type = await download(client, "http://a.co/")
    assert text == "" and content_type == "application/zip"


async def test_url_interna_avisa_sin_detalles():
    assert "no es una dirección pública" in await fetch_url_content("http://127.0.0.1:5432/")


def test_leer_html_quita_scripts_y_recorta():
    text = read_html(f"<title>T</title><script>x()</script><p>{'a' * 7000}</p>")
    assert text.startswith("Título: T") and "x()" not in text and "recortado" in text


def test_html_sin_titulo():
    assert read_html("<p>hola</p>").startswith("Título: \n")
