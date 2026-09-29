"""Pruebas de las estrategias de filtrado de catalogo.py.

Cada filtro es una función Libro -> bool, así que se prueba por separado con
un solo libro, sin armar un catálogo ni tocar archivos.
"""

import pytest

from libreria.catalogo import (
    aplicar_filtros,
    construir_filtros,
    filtrar_libros,
    por_autor,
    por_año_min,
    por_genero,
    por_precio_max,
    por_stock,
)
from libreria.modelos import Autor, Libreria, Libro


def _libro(isbn: str, **cambios: object) -> Libro:
    datos: dict[str, object] = {
        "isbn": isbn,
        "titulo": f"Libro {isbn}",
        "autor": Autor(nombre="Gabriel García Márquez", nacionalidad="Colombiana"),
        "genero": ["Realismo mágico", "Novela"],
        "año_publicacion": 1967,
        "precio": 250.0,
        "en_stock": True,
        "cantidad_disponible": 3,
        "editorial": "Sudamericana",
    }
    datos.update(cambios)
    return Libro.model_validate(datos)


@pytest.fixture
def libros() -> list[Libro]:
    return [
        _libro("1"),
        _libro("2", autor=Autor(nombre="Julio Cortázar"), genero=["Cuento"], precio=180.0),
        _libro("3", año_publicacion=1985, en_stock=False, cantidad_disponible=0, precio=320.0),
    ]


# ── Cada estrategia por separado ─────────────────────────────────────────────


def test_por_autor_ignora_mayusculas() -> None:
    libro = _libro("1")
    assert por_autor("garcía")(libro)
    assert not por_autor("cortázar")(libro)


def test_por_genero_busca_en_todos_los_generos() -> None:
    libro = _libro("1")
    assert por_genero("NOVELA")(libro)
    assert not por_genero("poesía")(libro)


def test_por_stock() -> None:
    assert por_stock(True)(_libro("1"))
    assert por_stock(False)(_libro("1", en_stock=False, cantidad_disponible=0))


def test_por_precio_max_incluye_el_limite() -> None:
    assert por_precio_max(250)(_libro("1"))
    assert not por_precio_max(249.99)(_libro("1"))


def test_por_año_min_incluye_el_limite() -> None:
    assert por_año_min(1967)(_libro("1"))
    assert not por_año_min(1968)(_libro("1"))


# ── Combinación de estrategias ───────────────────────────────────────────────


def test_construir_filtros_omite_criterios_en_none() -> None:
    assert construir_filtros() == []
    assert len(construir_filtros(autor="x", precio_max=100)) == 2


def test_sin_filtros_devuelve_todos(libros: list[Libro]) -> None:
    assert aplicar_filtros(libros, []) == libros


def test_los_filtros_se_combinan_con_y(libros: list[Libro]) -> None:
    filtros = construir_filtros(autor="garcía", en_stock=True)
    assert [lb.isbn for lb in aplicar_filtros(libros, filtros)] == ["1"]


def test_acepta_filtros_propios(libros: list[Libro]) -> None:
    """Cualquier función Libro -> bool sirve como estrategia."""

    def es_barato(libro: Libro) -> bool:
        return libro.precio < 200

    assert [lb.isbn for lb in aplicar_filtros(libros, [es_barato])] == ["2"]


def test_filtrar_libros_conserva_su_interfaz(libros: list[Libro]) -> None:
    data: Libreria = {
        "nombre": "Prueba",
        "direccion": {"calle": "", "colonia": "", "ciudad": "", "cp": ""},
        "telefono": "",
        "horario": "",
        "libros": libros,
    }
    resultado = filtrar_libros(data, precio_max=300, año_min=1960)
    assert [lb.isbn for lb in resultado] == ["1", "2"]
