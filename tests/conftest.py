"""Fixtures compartidas por las pruebas.

pytest carga este archivo automáticamente: cualquier prueba que declare un
parámetro llamado `sesion` recibe lo que devuelve la fixture de abajo.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from libreria import basedatos as bd
from libreria.almacenamiento import cargar_datos
from libreria.buscador import limpiar_cache_isbn

RAIZ = Path(__file__).parent.parent
# Catálogo FIJO para pruebas. No uses data/libreria.json: ese archivo cambia
# cada vez que usas el programa (p. ej. al importar un CSV) y rompería las pruebas.
CATALOGO = Path(__file__).parent / "datos" / "catalogo_prueba.json"


@pytest.fixture(autouse=True)
def _sin_cache_isbn() -> None:
    """Cada prueba empieza sin búsquedas de ISBN guardadas.

    La caché vive mientras corre el programa, y todas las pruebas corren en el
    mismo programa: sin esto, una prueba podría recibir lo que guardó otra.
    """
    limpiar_cache_isbn()


@pytest.fixture
def sesion() -> Iterator[Session]:
    """Base SQLite en memoria, nueva para cada prueba, con el catálogo importado."""
    motor = bd.crear_motor("sqlite://")  # "sqlite://" = base en memoria
    bd.crear_esquema(motor)
    with Session(motor) as s:
        bd.importar_catalogo(s, cargar_datos(CATALOGO))
        yield s
    motor.dispose()
