"""Consulta de datos de un libro en Open Library a partir de su ISBN.

Este es el único módulo del proyecto que habla con internet. Si la consulta
falla, lanza ServicioExternoError para que quien lo llame decida qué hacer
(en captura.py: seguir con la captura manual).

Usa la API REST de Open Library, que devuelve el JSON de cada objeto por su ruta:
  /isbn/9780307474728.json  -> la *edición* (título, editorial, fecha...)
  /works/OL274505W.json     -> la *obra* (géneros; a veces los autores)
  /authors/OL4586796A.json  -> el *autor* (nombre)
La edición solo trae la "clave" del autor, no su nombre; por eso hay que hacer
una petición extra por autor.

Las portadas están en otro servidor (covers.openlibrary.org) y se descargan
por streaming con descargar_portada().

Para descargar muchas portadas a la vez está descargar_portadas() (async): usa
httpx.AsyncClient y un asyncio.Semaphore para tener varias descargas en curso
sin saturar a Open Library.
"""

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from libreria.excepciones import ServicioExternoError
from libreria.utilidades import cache_temporal, reintentar, reintentar_async

log = logging.getLogger(__name__)

URL_BASE = "https://openlibrary.org"
URL_PORTADAS = "https://covers.openlibrary.org"
TIMEOUT_SEGUNDOS = 5
TIMEOUT_DESCARGA = 15  # una imagen tarda más que un JSON
TAMAÑO_TROZO = 64 * 1024  # 64 KB por trozo al descargar
MAX_GENEROS = 3
MAX_AUTORES = 3
# Open Library pide identificar la aplicación. Si agregas un correo de contacto,
# p. ej. "libreria/0.1 (tu@correo.com)", te permite más peticiones por segundo.
CABECERAS = {"User-Agent": "libreria/0.1 (proyecto del curso de Python)"}
# Cuántos ISBN se procesan a la vez en descargar_portadas(). Open Library pide
# no abusar de su API: con 5 se nota la mejora sin arriesgar un bloqueo.
MAX_DESCARGAS_SIMULTANEAS = 5
# Cuánto tiempo se recuerda la respuesta de Open Library para cada ISBN
SEGUNDOS_CACHE_ISBN = 600


class DatosISBN(BaseModel):
    """Datos que Open Library conoce de un libro.

    Todos los campos son opcionales porque la API no siempre los tiene. Son
    *sugerencias* para la captura, no un Libro completo: precio, cantidad y
    nacionalidad del autor siempre los captura el usuario.
    """

    titulo: str = ""
    autor: str = ""
    generos: list[str] = Field(default_factory=list)
    año_publicacion: int | None = None
    editorial: str = ""
    id_portada: int | None = None  # identificador de la portada en covers.openlibrary.org


def normalizar_isbn(isbn: str) -> str:
    """Quita guiones y espacios: '978-607-07-1234-5' -> '9786070712345'."""
    return re.sub(r"[\s-]", "", isbn).upper()


def _extraer_año(fecha: str) -> int | None:
    """Open Library da fechas como '1967', 'May 1967' o '1967-05-30'."""
    coincidencia = re.search(r"\b(\d{4})\b", fecha)
    return int(coincidencia.group(1)) if coincidencia else None


def _nombres(elementos: list[Any]) -> list[str]:
    """Algunas listas vienen como ["Vintage"] y otras como [{"name": "Vintage"}]."""
    resultado: list[str] = []
    for elemento in elementos:
        nombre = elemento.get("name", "") if isinstance(elemento, dict) else str(elemento)
        if nombre:
            resultado.append(nombre)
    return resultado


# Solo se reintentan los errores pasajeros de red (sin conexión, tiempo agotado).
# Un 500 o una respuesta rara no se arreglan volviendo a intentar.
@reintentar(
    intentos=2,
    espera_inicial=0.5,
    excepciones=(httpx.NetworkError, httpx.TimeoutException),
)
def _obtener_json(cliente: httpx.Client, ruta: str) -> dict[str, Any] | None:
    """Pide `ruta` al cliente y devuelve el JSON. Devuelve None si la respuesta es 404.

    En esta API un 404 no es un error: significa "no existe" (p. ej. un ISBN
    que Open Library no tiene registrado).
    """
    respuesta = cliente.get(ruta)  # la URL completa es base_url + ruta
    return _json_o_none(respuesta)


def _json_o_none(respuesta: httpx.Response) -> dict[str, Any] | None:
    """JSON de la respuesta, o None si es 404. Otros 4xx/5xx lanzan httpx.HTTPStatusError."""
    if respuesta.status_code == 404:
        return None
    respuesta.raise_for_status()
    datos: dict[str, Any] = respuesta.json()
    return datos


def _id_portada(edicion: dict[str, Any]) -> int | None:
    """Primer id de portada válido de la edición.

    "covers" es una lista de ids; Open Library usa -1 para portadas eliminadas.
    """
    return next((c for c in edicion.get("covers", []) if isinstance(c, int) and c > 0), None)


def _claves_autores(edicion: dict[str, Any], obra: dict[str, Any]) -> list[str]:
    """Claves de autor ('/authors/OL...A'). Se buscan primero en la edición y luego en la obra.

    Formatos: edición -> [{"key": ...}], obra -> [{"author": {"key": ...}}].
    """
    claves = [a["key"] for a in edicion.get("authors", []) if "key" in a]
    if not claves:
        claves = [
            a["author"]["key"]
            for a in obra.get("authors", [])
            if isinstance(a.get("author"), dict) and "key" in a["author"]
        ]
    return claves[:MAX_AUTORES]


def buscar_por_isbn(isbn: str, transporte: httpx.BaseTransport | None = None) -> DatosISBN | None:
    """Busca un libro por ISBN en Open Library.

    Devuelve None si el ISBN no está registrado. Lanza ServicioExternoError si
    no se pudo consultar (sin internet, servidor caído, respuesta inválida).

    Las respuestas (también "no encontrado") se recuerdan SEGUNDOS_CACHE_ISBN
    segundos: repetir la búsqueda del mismo ISBN no vuelve a usar la red. Los
    errores no se recuerdan.

    `transporte` solo se usa en las pruebas, para responder sin internet
    (httpx.MockTransport). En el programa se deja en None: httpx usa la red.
    """
    # Se normaliza ANTES de la caché: con o sin guiones es la misma entrada
    datos = _buscar_isbn_normalizado(normalizar_isbn(isbn), transporte)
    # La caché guarda un solo objeto; se entrega una copia para que quien la
    # reciba pueda modificarla sin alterar lo que quedó guardado.
    return datos.model_copy(deep=True) if datos is not None else None


def limpiar_cache_isbn() -> None:
    """Olvida las búsquedas guardadas (lo usan las pruebas)."""
    _buscar_isbn_normalizado.limpiar()


@cache_temporal(segundos=SEGUNDOS_CACHE_ISBN)
def _buscar_isbn_normalizado(
    isbn_limpio: str, transporte: httpx.BaseTransport | None
) -> DatosISBN | None:
    """La consulta real a Open Library. `isbn_limpio` ya viene normalizado."""
    log.info("Consultando Open Library: ISBN %s", isbn_limpio)

    try:
        # Un solo Client para todas las peticiones: reutiliza la conexión y
        # comparte la configuración. El `with` la cierra al terminar.
        with httpx.Client(
            base_url=URL_BASE,
            headers=CABECERAS,
            timeout=TIMEOUT_SEGUNDOS,
            follow_redirects=True,  # httpx NO sigue redirecciones por defecto
            transport=transporte,
        ) as cliente:
            # 1. La edición. /isbn/... redirige a /books/OL...M.json
            edicion = _obtener_json(cliente, f"/isbn/{isbn_limpio}.json")
            if edicion is None:
                log.info("ISBN no encontrado en Open Library: %s", isbn_limpio)
                return None

            # 2. La obra (opcional): de aquí salen los géneros si la edición no los trae
            obras = edicion.get("works", [])
            obra = (_obtener_json(cliente, f"{obras[0]['key']}.json") if obras else None) or {}

            # 3. Un GET por autor para obtener su nombre
            autores: list[str] = []
            for clave in _claves_autores(edicion, obra):
                autor = _obtener_json(cliente, f"{clave}.json") or {}
                if autor.get("name"):
                    autores.append(autor["name"])
    except httpx.HTTPError as e:
        # HTTPError es la base de los errores de httpx: de red y de código 4xx/5xx
        raise ServicioExternoError(f"No se pudo consultar Open Library: {e}") from e
    except json.JSONDecodeError as e:
        # A diferencia de requests, httpx no envuelve este error: .json() lanza
        # directamente el de la biblioteca estándar.
        raise ServicioExternoError(f"Open Library respondió algo que no es JSON: {e}") from e

    generos = _nombres(edicion.get("subjects") or obra.get("subjects", []))
    datos = DatosISBN(
        titulo=edicion.get("title") or obra.get("title", ""),
        autor=", ".join(autores),
        generos=generos[:MAX_GENEROS],
        año_publicacion=_extraer_año(edicion.get("publish_date", "")),
        editorial=next(iter(_nombres(edicion.get("publishers", []))), ""),
        id_portada=_id_portada(edicion),
    )
    log.info("Encontrado en Open Library: %s", datos.titulo)
    return datos


def nombre_archivo_portada(isbn: str) -> str:
    """Nombre de archivo seguro para la portada: '978-0-307-47472-8' -> '9780307474728.jpg'.

    Solo deja letras y números, para que un ISBN mal escrito (con '/', por
    ejemplo) no pueda crear rutas raras.
    """
    return f"{re.sub(r'[^0-9A-Za-z]', '', isbn) or 'portada'}.jpg"


@reintentar(
    intentos=2,
    espera_inicial=0.5,
    excepciones=(httpx.NetworkError, httpx.TimeoutException),
)
def _descargar_a_archivo(cliente: httpx.Client, ruta: str, archivo: Path) -> int:
    """Descarga `ruta` por streaming y la escribe en `archivo`, trozo por trozo.

    Devuelve los bytes escritos. Si se reintenta, "wb" vuelve a empezar el
    archivo desde cero.
    """
    escritos = 0
    # cliente.stream() no descarga el cuerpo al hacer la petición: se lee en el for
    with cliente.stream("GET", ruta) as respuesta:
        respuesta.raise_for_status()
        with open(archivo, "wb") as f:
            for trozo in respuesta.iter_bytes(chunk_size=TAMAÑO_TROZO):
                f.write(trozo)
                escritos += len(trozo)
    return escritos


def descargar_portada(
    id_portada: int, destino: Path, transporte: httpx.BaseTransport | None = None
) -> Path:
    """Descarga la portada `id_portada` (tamaño grande) a `destino` por streaming.

    Se escribe primero en un archivo `.part` y solo se renombra a `destino` si
    la descarga terminó completa; si falla, no queda ningún archivo a medias.
    Lanza ServicioExternoError si no se pudo descargar.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(destino.name + ".part")
    log.info("Descargando portada %d a %s", id_portada, destino)

    try:
        with httpx.Client(
            base_url=URL_PORTADAS,
            headers=CABECERAS,
            timeout=TIMEOUT_DESCARGA,
            follow_redirects=True,  # la imagen real está alojada en archive.org
            transport=transporte,
        ) as cliente:
            # /b/id/ en lugar de /b/isbn/: por ISBN, Open Library limita las consultas
            escritos = _descargar_a_archivo(cliente, f"/b/id/{id_portada}-L.jpg", temporal)
        temporal.replace(destino)  # solo llega aquí si la descarga terminó
    except httpx.HTTPError as e:
        raise ServicioExternoError(f"No se pudo descargar la portada: {e}") from e
    except OSError as e:  # disco lleno, sin permisos en la carpeta, etc.
        raise ServicioExternoError(f"No se pudo guardar la portada: {e}") from e
    finally:
        temporal.unlink(missing_ok=True)  # si falló, borra el archivo a medias

    log.info("Portada guardada: %s (%d bytes)", destino, escritos)
    return destino


# ── Descarga concurrente de portadas (asyncio) ────────────────────────────────
#
# Mismo flujo que buscar_por_isbn() + descargar_portada(), pero para muchos ISBN
# a la vez. Cada ISBN necesita dos peticiones: su edición (para saber el id de la
# portada) y la imagen. Mientras una corrutina espera la red, el event loop
# avanza con las demás; el semáforo limita cuántas están en curso.


@dataclass
class ResultadoPortadas:
    """Qué pasó con cada ISBN al descargar portadas en lote."""

    descargadas: dict[str, Path] = field(default_factory=dict)
    sin_portada: list[str] = field(default_factory=list)  # no está en Open Library o sin imagen
    errores: dict[str, str] = field(default_factory=dict)  # ISBN -> motivo


@reintentar_async(
    intentos=2,
    espera_inicial=0.5,
    excepciones=(httpx.NetworkError, httpx.TimeoutException),
)
async def _obtener_json_async(cliente: httpx.AsyncClient, ruta: str) -> dict[str, Any] | None:
    """Versión async de _obtener_json()."""
    respuesta = await cliente.get(ruta)
    return _json_o_none(respuesta)


@reintentar_async(
    intentos=2,
    espera_inicial=0.5,
    excepciones=(httpx.NetworkError, httpx.TimeoutException),
)
async def _descargar_a_archivo_async(cliente: httpx.AsyncClient, ruta: str, archivo: Path) -> int:
    """Versión async de _descargar_a_archivo(): streaming trozo por trozo.

    La escritura en disco es síncrona (open/write normales). Con trozos de
    64 KB bloquea el event loop solo un instante, así que no vale la pena
    mandarla a un hilo con asyncio.to_thread().
    """
    escritos = 0
    async with cliente.stream("GET", ruta) as respuesta:
        respuesta.raise_for_status()
        with open(archivo, "wb") as f:
            async for trozo in respuesta.aiter_bytes(chunk_size=TAMAÑO_TROZO):
                f.write(trozo)
                escritos += len(trozo)
    return escritos


async def _portada_de_isbn(
    cliente_ol: httpx.AsyncClient,
    cliente_portadas: httpx.AsyncClient,
    semaforo: asyncio.Semaphore,
    isbn: str,
    destino: Path,
) -> Path | None:
    """Busca la edición de `isbn` y descarga su portada a `destino`.

    Devuelve None si Open Library no tiene el ISBN o no tiene portada.
    Lanza ServicioExternoError si algo falla.
    """
    # Solo `MAX_DESCARGAS_SIMULTANEAS` corrutinas pasan de aquí a la vez; las
    # demás esperan (sin bloquear el loop) a que alguna salga del `async with`.
    async with semaforo:
        temporal = destino.with_name(destino.name + ".part")
        try:
            edicion = await _obtener_json_async(cliente_ol, f"/isbn/{normalizar_isbn(isbn)}.json")
            id_portada = _id_portada(edicion) if edicion is not None else None
            if id_portada is None:
                log.info("Sin portada en Open Library: ISBN %s", isbn)
                return None

            escritos = await _descargar_a_archivo_async(
                cliente_portadas, f"/b/id/{id_portada}-L.jpg", temporal
            )
            temporal.replace(destino)  # solo llega aquí si la descarga terminó
        except httpx.HTTPError as e:
            raise ServicioExternoError(f"No se pudo descargar la portada: {e}") from e
        except json.JSONDecodeError as e:
            raise ServicioExternoError(f"Open Library respondió algo que no es JSON: {e}") from e
        except OSError as e:
            raise ServicioExternoError(f"No se pudo guardar la portada: {e}") from e
        finally:
            temporal.unlink(missing_ok=True)

    log.info("Portada guardada: %s (%d bytes)", destino, escritos)
    return destino


async def descargar_portadas(
    isbns: Iterable[str],
    carpeta: Path,
    max_simultaneas: int = MAX_DESCARGAS_SIMULTANEAS,
    transporte: httpx.AsyncBaseTransport | None = None,
) -> ResultadoPortadas:
    """Descarga en paralelo las portadas de varios ISBN a `carpeta/<isbn>.jpg`.

    Que falle un ISBN no detiene a los demás: cada error queda anotado en el
    resultado. Por eso se usa asyncio.gather(return_exceptions=True) y no un
    TaskGroup, que cancelaría todas las descargas al primer error.

    Desde código síncrono (como main.py) se llama con asyncio.run().
    `transporte` solo se usa en las pruebas (httpx.MockTransport).
    """
    if max_simultaneas < 1:
        raise ValueError("max_simultaneas debe ser al menos 1")

    pendientes = list(dict.fromkeys(isbns))  # sin repetidos, en el mismo orden
    resultado = ResultadoPortadas()
    if not pendientes:
        return resultado

    carpeta.mkdir(parents=True, exist_ok=True)
    semaforo = asyncio.Semaphore(max_simultaneas)
    log.info("Descargando %d portada(s), máximo %d a la vez", len(pendientes), max_simultaneas)

    # Un cliente por servidor, compartido por todas las corrutinas: así se
    # reutilizan las conexiones en lugar de abrir una nueva por cada ISBN.
    async with (
        httpx.AsyncClient(
            base_url=URL_BASE,
            headers=CABECERAS,
            timeout=TIMEOUT_SEGUNDOS,
            follow_redirects=True,
            transport=transporte,
        ) as cliente_ol,
        httpx.AsyncClient(
            base_url=URL_PORTADAS,
            headers=CABECERAS,
            timeout=TIMEOUT_DESCARGA,
            follow_redirects=True,
            transport=transporte,
        ) as cliente_portadas,
    ):
        respuestas = await asyncio.gather(
            *(
                _portada_de_isbn(
                    cliente_ol,
                    cliente_portadas,
                    semaforo,
                    isbn,
                    carpeta / nombre_archivo_portada(isbn),
                )
                for isbn in pendientes
            ),
            return_exceptions=True,
        )

    for isbn, respuesta in zip(pendientes, respuestas, strict=True):
        match respuesta:
            case Path():
                resultado.descargadas[isbn] = respuesta
            case None:
                resultado.sin_portada.append(isbn)
            case ServicioExternoError():
                log.warning("ISBN %s: %s", isbn, respuesta)
                resultado.errores[isbn] = str(respuesta)
            case _:
                # Cualquier otra excepción es un error de programación (o una
                # cancelación, p. ej. Ctrl+C): no se esconde en el resultado.
                raise respuesta

    log.info(
        "Portadas: %d descargadas, %d sin portada, %d con error",
        len(resultado.descargadas),
        len(resultado.sin_portada),
        len(resultado.errores),
    )
    return resultado
