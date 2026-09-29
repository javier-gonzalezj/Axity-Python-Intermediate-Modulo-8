"""Pruebas de la descarga concurrente de portadas y de @reintentar_async.

Igual que en test_buscador.py, httpx.MockTransport responde en lugar de Open
Library, así que no se necesita internet. MockTransport sirve también para
httpx.AsyncClient, y el `responder` puede ser una función normal o `async def`.

Las pruebas son funciones normales que ejecutan la corrutina con asyncio.run(),
así no hace falta instalar ningún plugin de pytest para código async.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest

from libreria import buscador, main, utilidades
from libreria.almacenamiento import cargar_datos
from tests.conftest import CATALOGO

# Se guarda la función original ANTES de que la fixture `sin_esperas` la
# reemplace: los transportes falsos la usan para simular una red lenta.
dormir = asyncio.sleep

IMAGEN_FALSA = bytes(range(256)) * 800  # 200 KB: llega en varios trozos de 64 KB

# ISBN -> id de portada. Los que no aparecen aquí no existen (404).
EDICIONES: dict[str, int | None] = {
    "9780307474728": 8231856,
    "9780679723165": 1234567,
    "9786070000001": 7654321,
    "9780000000002": None,  # la edición existe, pero sin portada
}


def ruta_edicion(isbn: str) -> str:
    return f"/isbn/{isbn}.json"


def responder_open_library(peticion: httpx.Request) -> httpx.Response:
    """Imita openlibrary.org (ediciones) y covers.openlibrary.org (imágenes)."""
    ruta = peticion.url.path
    if peticion.url.host == "openlibrary.org":
        isbn = ruta.removeprefix("/isbn/").removesuffix(".json")
        if isbn not in EDICIONES:
            return httpx.Response(404)
        id_portada = EDICIONES[isbn]
        return httpx.Response(200, json={"covers": [id_portada] if id_portada else []})
    if peticion.url.host == "covers.openlibrary.org" and ruta.endswith("-L.jpg"):
        return httpx.Response(200, content=IMAGEN_FALSA)
    return httpx.Response(404)


def transporte_async(
    responder: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """MockTransport que además guarda cada petición, para contarlas."""
    peticiones: list[httpx.Request] = []

    async def registrar(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        respuesta = responder(peticion)
        if isinstance(respuesta, httpx.Response):
            return respuesta
        return await respuesta

    return httpx.MockTransport(registrar), peticiones


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita que @reintentar_async espere de verdad entre intentos."""

    async def no_esperar(_segundos: float) -> None:
        return None

    monkeypatch.setattr(utilidades.asyncio, "sleep", no_esperar)


# ── @reintentar_async ──────────────────────────────────────────────────────


def test_reintentar_async_reintenta_hasta_lograrlo() -> None:
    llamadas = 0

    @utilidades.reintentar_async(intentos=3, excepciones=(ConnectionError,))
    async def inestable() -> str:
        nonlocal llamadas
        llamadas += 1
        if llamadas < 3:
            raise ConnectionError("falla pasajera")
        return "listo"

    assert asyncio.run(inestable()) == "listo"
    assert llamadas == 3


def test_reintentar_async_propaga_tras_el_ultimo_intento() -> None:
    llamadas = 0

    @utilidades.reintentar_async(intentos=2, excepciones=(ConnectionError,))
    async def siempre_falla() -> None:
        nonlocal llamadas
        llamadas += 1
        raise ConnectionError("sin red")

    with pytest.raises(ConnectionError):
        asyncio.run(siempre_falla())
    assert llamadas == 2


def test_reintentar_async_no_reintenta_otras_excepciones() -> None:
    llamadas = 0

    @utilidades.reintentar_async(intentos=3, excepciones=(ConnectionError,))
    async def error_de_datos() -> None:
        nonlocal llamadas
        llamadas += 1
        raise ValueError("dato inválido")

    with pytest.raises(ValueError):
        asyncio.run(error_de_datos())
    assert llamadas == 1


# ── descargar_portadas ─────────────────────────────────────────────────────


def descargar(
    isbns: list[str],
    carpeta: Path,
    transporte: httpx.MockTransport,
    max_simultaneas: int = buscador.MAX_DESCARGAS_SIMULTANEAS,
) -> buscador.ResultadoPortadas:
    return asyncio.run(buscador.descargar_portadas(isbns, carpeta, max_simultaneas, transporte))


def test_descarga_varias_y_clasifica(tmp_path: Path) -> None:
    transporte, _ = transporte_async(responder_open_library)
    carpeta = tmp_path / "portadas"  # aún no existe: se crea

    resultado = descargar(
        # con guiones, uno sin portada y uno que Open Library no conoce
        ["978-0-307-47472-8", "9780679723165", "9780000000002", "9999999999"],
        carpeta,
        transporte,
    )

    assert resultado.descargadas == {
        "978-0-307-47472-8": carpeta / "9780307474728.jpg",
        "9780679723165": carpeta / "9780679723165.jpg",
    }
    assert resultado.sin_portada == ["9780000000002", "9999999999"]
    assert resultado.errores == {}
    assert (carpeta / "9780307474728.jpg").read_bytes() == IMAGEN_FALSA
    assert sorted(p.name for p in carpeta.iterdir()) == ["9780307474728.jpg", "9780679723165.jpg"]


def test_un_error_no_detiene_a_los_demas(tmp_path: Path) -> None:
    def con_un_error(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path == ruta_edicion("9780679723165"):
            return httpx.Response(500)
        return responder_open_library(peticion)

    transporte, _ = transporte_async(con_un_error)

    resultado = descargar(["9780307474728", "9780679723165", "9786070000001"], tmp_path, transporte)

    assert set(resultado.descargadas) == {"9780307474728", "9786070000001"}
    assert list(resultado.errores) == ["9780679723165"]
    assert "500" in resultado.errores["9780679723165"]


def test_el_semaforo_limita_las_descargas_simultaneas(tmp_path: Path) -> None:
    activas = 0
    maximo = 0

    async def red_lenta(peticion: httpx.Request) -> httpx.Response:
        nonlocal activas, maximo
        activas += 1
        maximo = max(maximo, activas)
        await dormir(0.01)  # mientras "espera la red", el loop atiende a otras corrutinas
        activas -= 1
        return httpx.Response(404)  # ningún ISBN existe: solo interesa contar

    transporte, peticiones = transporte_async(red_lenta)
    isbns = [f"97800000000{n:02d}" for n in range(10)]

    resultado = descargar(isbns, tmp_path, transporte, max_simultaneas=3)

    assert len(peticiones) == 10
    assert maximo == 3  # hubo concurrencia, pero nunca más de 3 a la vez
    assert resultado.sin_portada == isbns


def test_isbn_repetido_se_descarga_una_vez(tmp_path: Path) -> None:
    transporte, peticiones = transporte_async(responder_open_library)

    resultado = descargar(["9780307474728", "9780307474728"], tmp_path, transporte)

    assert list(resultado.descargadas) == ["9780307474728"]
    assert len(peticiones) == 2  # una edición + una imagen


def test_lista_vacia_no_hace_peticiones(tmp_path: Path) -> None:
    transporte, peticiones = transporte_async(responder_open_library)
    carpeta = tmp_path / "portadas"

    resultado = descargar([], carpeta, transporte)

    assert resultado == buscador.ResultadoPortadas()
    assert peticiones == []
    assert not carpeta.exists()


class CorteDeRed(httpx.AsyncByteStream):
    """Cuerpo de respuesta async que envía un poco de datos y luego se corta."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"x" * 1000
        raise httpx.ReadError("se cortó la conexión")


def test_corte_de_red_reintenta_y_no_deja_archivos(tmp_path: Path) -> None:
    def imagen_cortada(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.host == "covers.openlibrary.org":
            return httpx.Response(200, stream=CorteDeRed())
        return responder_open_library(peticion)

    transporte, peticiones = transporte_async(imagen_cortada)

    resultado = descargar(["9780307474728"], tmp_path, transporte)

    assert list(resultado.errores) == ["9780307474728"]
    imagenes = [p for p in peticiones if p.url.host == "covers.openlibrary.org"]
    assert len(imagenes) == 2  # el corte se reintenta una vez
    assert list(tmp_path.iterdir()) == []  # ni la imagen ni el .part


def test_max_simultaneas_invalido(tmp_path: Path) -> None:
    transporte, _ = transporte_async(responder_open_library)

    with pytest.raises(ValueError):
        descargar(["9780307474728"], tmp_path, transporte, max_simultaneas=0)


def test_error_inesperado_no_se_esconde(tmp_path: Path) -> None:
    """Un error que no es de red (un bug) se propaga en lugar de quedar en `errores`."""

    def con_bug(peticion: httpx.Request) -> httpx.Response:
        raise KeyError("bug en el código")

    transporte, _ = transporte_async(con_bug)

    with pytest.raises(KeyError):
        descargar(["9780307474728"], tmp_path, transporte)


def test_id_portada() -> None:
    """La versión síncrona y la async usan la misma función para el id."""
    assert buscador._id_portada({"covers": [-1, 8231856]}) == 8231856  # -1 = eliminada
    assert buscador._id_portada({"covers": []}) is None
    assert buscador._id_portada({}) is None


# ── main.py: qué libros no tienen portada ──────────────────────────────────


def test_isbns_sin_portada(tmp_path: Path) -> None:
    catalogo = cargar_datos(CATALOGO)
    con_portada, *resto = catalogo["libros"]
    (tmp_path / buscador.nombre_archivo_portada(con_portada.isbn)).write_bytes(b"jpg")

    faltantes = main._isbns_sin_portada(catalogo["libros"], tmp_path)

    assert faltantes == [libro.isbn for libro in resto]
