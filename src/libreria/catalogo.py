"""Reglas del catálogo: operaciones sobre los libros sin entrada/salida."""

import logging
from collections.abc import Callable, Iterable

from libreria.excepciones import LibroDuplicadoError
from libreria.modelos import Libreria, Libro

log = logging.getLogger(__name__)

# Una estrategia de filtrado: decide si un libro se queda (True) o no (False).
type Filtro = Callable[[Libro], bool]


def agregar_libro(data: Libreria, libro: Libro) -> Libreria:
    """Agrega un nuevo libro al catálogo, verificando que el ISBN no exista ya.

    La validación de la estructura ya la hizo el modelo Libro al crearse.
    """
    isbn_existentes = {existente.isbn for existente in data["libros"]}
    if libro.isbn in isbn_existentes:
        raise LibroDuplicadoError(f"Ya existe un libro con ISBN {libro.isbn}")

    data["libros"].append(libro)
    log.info("Libro agregado: %s (ISBN %s)", libro.titulo, libro.isbn)

    return data


# ── Estrategias de filtrado (patrón Strategy) ────────────────────────────────
# Cada función recibe el criterio y DEVUELVE otra función: el filtro. El
# criterio queda "guardado" dentro de ella (closure). Todas cumplen el tipo
# Filtro, así que aplicar_filtros() las trata igual sin saber qué revisan.


def por_autor(texto: str) -> Filtro:
    """Libros cuyo autor contiene `texto` (sin distinguir mayúsculas)."""
    buscado = texto.lower()
    return lambda libro: buscado in libro.autor.nombre.lower()


def por_genero(texto: str) -> Filtro:
    """Libros con algún género que contenga `texto` (sin distinguir mayúsculas)."""
    buscado = texto.lower()
    return lambda libro: any(buscado in g.lower() for g in libro.genero)


def por_stock(en_stock: bool) -> Filtro:
    """Libros disponibles (True) o agotados (False)."""
    return lambda libro: libro.en_stock == en_stock


def por_precio_max(precio: float) -> Filtro:
    """Libros que cuestan `precio` o menos."""
    return lambda libro: libro.precio <= precio


def por_año_min(año: int) -> Filtro:
    """Libros publicados en `año` o después."""
    return lambda libro: libro.año_publicacion >= año


def construir_filtros(
    autor: str | None = None,
    genero: str | None = None,
    en_stock: bool | None = None,
    precio_max: float | None = None,
    año_min: int | None = None,
) -> list[Filtro]:
    """Convierte los criterios opcionales en una lista de estrategias.

    Los criterios en None no generan filtro. Es el único lugar donde se decide
    qué significa cada parámetro; la consola y la API lo comparten.
    """
    filtros: list[Filtro] = []
    if autor is not None:
        filtros.append(por_autor(autor))
    if genero is not None:
        filtros.append(por_genero(genero))
    if en_stock is not None:
        filtros.append(por_stock(en_stock))
    if precio_max is not None:
        filtros.append(por_precio_max(precio_max))
    if año_min is not None:
        filtros.append(por_año_min(año_min))
    return filtros


def aplicar_filtros(libros: Iterable[Libro], filtros: Iterable[Filtro]) -> list[Libro]:
    """Libros que pasan TODOS los filtros. Sin filtros, devuelve todos."""
    # Se recorre una vez por libro, así que no puede quedarse como generador
    lista_filtros = list(filtros)
    return [libro for libro in libros if all(f(libro) for f in lista_filtros)]


def filtrar_libros(
    data: Libreria,
    autor: str | None = None,
    genero: str | None = None,
    en_stock: bool | None = None,
    precio_max: float | None = None,
    año_min: int | None = None,
) -> list[Libro]:
    """Filtra el catálogo de libros según los criterios indicados.

    Cualquier parámetro que se deje en None se ignora (no filtra por ese campo).
    """
    log.debug(
        "Filtros: autor=%r genero=%r en_stock=%r precio_max=%r año_min=%r",
        autor,
        genero,
        en_stock,
        precio_max,
        año_min,
    )
    filtros = construir_filtros(autor, genero, en_stock, precio_max, año_min)
    resultado = aplicar_filtros(data["libros"], filtros)
    log.debug("Filtrado: %d de %d libros", len(resultado), len(data["libros"]))

    return resultado
