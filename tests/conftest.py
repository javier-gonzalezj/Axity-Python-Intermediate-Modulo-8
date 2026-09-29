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
from libreria.casos_uso import (
    AgregarLibro,
    CambiarEstatusComando,
    CambiarEstatusPedido,
    CrearPedido,
    CrearPedidoComando,
)
from libreria.modelos import Libro
from libreria.pedidos import Pedido
from libreria.repositorios_sql import UnidadDeTrabajoSQL

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


# ── Atajos: los casos de uso REALES sobre la base de una prueba ─────────────
# Las pruebas de basedatos, CRUD y predicción necesitan pedidos en la base.
# En lugar de insertarlos a mano, usan el mismo camino que la API.


def crear_pedido(sesion: Session, usuario_id: int, lineas: dict[str, int]) -> Pedido:
    comando = CrearPedidoComando(usuario_id=usuario_id, lineas=lineas)
    return CrearPedido(UnidadDeTrabajoSQL(sesion)).ejecutar(comando)


def cambiar_estatus(sesion: Session, pedido_id: int | None, estatus: str) -> Pedido:
    assert pedido_id is not None
    comando = CambiarEstatusComando(pedido_id=pedido_id, estatus=estatus)
    return CambiarEstatusPedido(UnidadDeTrabajoSQL(sesion)).ejecutar(comando)


def cancelar_pedido(sesion: Session, pedido_id: int | None) -> Pedido:
    return cambiar_estatus(sesion, pedido_id, "cancelado")


def agregar_libro(sesion: Session, libro: Libro) -> Libro:
    return AgregarLibro(UnidadDeTrabajoSQL(sesion)).ejecutar(libro)
