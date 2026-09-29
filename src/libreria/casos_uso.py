"""Casos de uso: las acciones que el sistema ofrece (capa de aplicación).

Cada caso de uso ORQUESTA; no decide reglas:

1. obtiene lo que necesita a través de los puertos (UnidadDeTrabajo),
2. le deja las decisiones al dominio (pedidos.py, Libro),
3. guarda el resultado y confirma todo junto o nada.

No saben nada de HTTP ni de SQLAlchemy: la API construye un comando, llama a
ejecutar() y traduce el resultado (o la excepción) a una respuesta. Por eso se
prueban con UnidadDeTrabajoEnMemoria (tests/test_casos_uso.py), y el contrato
(tests/test_contrato_repositorios.py) garantiza que con la base real funcionan igual.

Las reglas de QUIÉN puede hacer cada cosa (dueño del pedido, rol admin) se
quedan por ahora en la API (api/dependencies.py), porque dependen del token.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from libreria import pedidos
from libreria.excepciones import PedidoNoEncontradoError, UsuarioNoEncontradoError
from libreria.modelos import Libro
from libreria.pedidos import Pedido
from libreria.puertos import UnidadDeTrabajo

# El reloj también es una dependencia: en las pruebas se pasa uno fijo
type Reloj = Callable[[], datetime]


# ── Comandos: lo que entra a cada caso de uso ────────────────────────────────


@dataclass(frozen=True)
class CrearPedidoComando:
    usuario_id: int
    lineas: Mapping[str, int]  # {isbn: cantidad}


@dataclass(frozen=True)
class CambiarEstatusComando:
    pedido_id: int
    estatus: str


# ── Casos de uso ─────────────────────────────────────────────────────────────


class CrearPedido:
    """Un usuario pide libros: se descuenta el stock y se registra el pedido."""

    def __init__(self, uow: UnidadDeTrabajo, ahora: Reloj = datetime.now) -> None:
        self._uow = uow
        self._ahora = ahora

    def ejecutar(self, cmd: CrearPedidoComando) -> Pedido:
        with self._uow as uow:
            if uow.usuarios.obtener(cmd.usuario_id) is None:
                raise UsuarioNoEncontradoError(f"No existe el usuario {cmd.usuario_id}")

            libros = uow.libros.obtener_varios(cmd.lineas)
            # El dominio valida y descuenta (o lanza el error sin tocar nada)
            pedido = pedidos.crear_pedido(cmd.usuario_id, cmd.lineas, libros, fecha=self._ahora())

            for isbn in cmd.lineas:
                uow.libros.guardar(libros[isbn])
            uow.pedidos.agregar(pedido)
            uow.confirmar()
            return _releer(uow, pedido)


class CambiarEstatusPedido:
    """Avanza un pedido (pagado, enviado) o lo cancela regresando el stock."""

    def __init__(self, uow: UnidadDeTrabajo) -> None:
        self._uow = uow

    def ejecutar(self, cmd: CambiarEstatusComando) -> Pedido:
        with self._uow as uow:
            pedido = uow.pedidos.obtener(cmd.pedido_id)
            if pedido is None:
                raise PedidoNoEncontradoError(f"No existe el pedido {cmd.pedido_id}")

            libros = uow.libros.obtener_varios(item.isbn for item in pedido.items)
            anterior = pedido.estatus
            pedidos.cambiar_estatus(pedido, cmd.estatus, libros)  # el dominio decide

            if pedido.estatus != anterior:  # cancelar dos veces no cambia nada
                for libro in libros.values():
                    uow.libros.guardar(libro)
                uow.pedidos.guardar(pedido)
                uow.confirmar()
            return _releer(uow, pedido)


class AgregarLibro:
    """Un administrador da de alta un libro. LibroDuplicadoError si el ISBN ya existe."""

    def __init__(self, uow: UnidadDeTrabajo) -> None:
        self._uow = uow

    def ejecutar(self, libro: Libro) -> Libro:
        with self._uow as uow:
            uow.libros.agregar(libro)
            uow.confirmar()
        return libro


def _releer(uow: UnidadDeTrabajo, pedido: Pedido) -> Pedido:
    """El pedido tal como quedó guardado (con id y en el formato del repositorio)."""
    assert pedido.id is not None  # agregar() ya le asignó uno
    guardado = uow.pedidos.obtener(pedido.id)
    assert guardado is not None
    return guardado
