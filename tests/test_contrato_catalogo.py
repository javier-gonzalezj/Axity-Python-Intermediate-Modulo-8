"""Pruebas de CONTRATO del puerto RepositorioCatalogo.

Cada prueba corre contra CatalogoJSON (un archivo real, en una carpeta
temporal) y contra CatalogoEnMemoria, que es el que usan las pruebas de
ServicioCatalogo. Si las dos pasan, esas pruebas no mienten.

Lo que el contrato NO cubre: los errores de archivo (sin permisos, JSON
dañado...). Solo existen en CatalogoJSON y se prueban en test_servicios.py.
"""

import shutil
from pathlib import Path

import pytest

from libreria.almacenamiento import CatalogoJSON, cargar_datos
from libreria.modelos import Autor, Libro
from libreria.puertos import RepositorioCatalogo
from libreria.repositorios_memoria import CatalogoEnMemoria
from tests.conftest import CATALOGO


@pytest.fixture(params=["json", "memoria"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> RepositorioCatalogo:
    """El mismo catálogo inicial en los dos adaptadores."""
    if request.param == "json":
        copia = tmp_path / "catalogo.json"
        shutil.copy(CATALOGO, copia)  # nunca se toca el archivo original
        return CatalogoJSON(copia)
    return CatalogoEnMemoria(cargar_datos(CATALOGO))


def libro_nuevo() -> Libro:
    return Libro(
        isbn="978-607-16-0001-1",
        titulo="Pedro Páramo",
        autor=Autor(nombre="Juan Rulfo", nacionalidad="Mexicana"),
        genero=["Novela"],
        año_publicacion=1955,
        precio=189.0,
        en_stock=True,
        cantidad_disponible=10,
        editorial="Fondo de Cultura Económica",
    )


def isbns(repo: RepositorioCatalogo) -> set[str]:
    return {libro.isbn for libro in repo.cargar()["libros"]}


def test_cargar_devuelve_libros_ya_validados(repo: RepositorioCatalogo) -> None:
    data = repo.cargar()

    assert data["libros"]
    assert all(isinstance(libro, Libro) for libro in data["libros"])
    assert data["nombre"]  # también los datos de la librería


def test_lo_guardado_se_vuelve_a_cargar_igual(repo: RepositorioCatalogo) -> None:
    data = repo.cargar()
    data["libros"].append(libro_nuevo())

    repo.guardar(data)

    assert repo.cargar() == data  # ida y vuelta sin perder nada


def test_guardar_reemplaza_el_catalogo_completo(repo: RepositorioCatalogo) -> None:
    data = repo.cargar()
    quitado = data["libros"].pop()

    repo.guardar(data)

    assert quitado.isbn not in isbns(repo)


def test_los_cambios_sin_guardar_no_se_ven_al_cargar(repo: RepositorioCatalogo) -> None:
    data = repo.cargar()
    data["libros"].append(libro_nuevo())
    data["libros"][0].precio = 1.0
    # sin repo.guardar(data)

    recargado = repo.cargar()
    assert libro_nuevo().isbn not in {libro.isbn for libro in recargado["libros"]}
    assert recargado["libros"][0].precio != 1.0


def test_modificar_lo_guardado_despues_no_cambia_el_almacen(repo: RepositorioCatalogo) -> None:
    data = repo.cargar()
    repo.guardar(data)

    data["libros"].clear()  # el objeto ya se guardó; cambiarlo no debe afectar

    assert isbns(repo)
