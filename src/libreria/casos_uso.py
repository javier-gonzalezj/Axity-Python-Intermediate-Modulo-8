"""Casos de uso: las acciones que el sistema ofrece (capa de aplicación).

Cada caso de uso ORQUESTA; no decide reglas:

1. obtiene lo que necesita a través de los puertos (UnidadDeTrabajo),
2. le deja las decisiones al dominio (pedidos.py, Libro),
3. guarda el resultado y confirma todo junto o nada,
4. y solo entonces avisa al cliente (puerto Notificador).

El aviso va DESPUÉS de confirmar y fuera de la transacción: no se avisa de algo
que luego se revirtió. Y si el aviso falla, el pedido ya quedó guardado: solo
se registra una advertencia en el log, no se deshace nada.

No saben nada de HTTP ni de SQLAlchemy: la API construye un comando, llama a
ejecutar() y traduce el resultado (o la excepción) a una respuesta. Por eso se
prueban con UnidadDeTrabajoEnMemoria (tests/test_casos_uso.py), y el contrato
(tests/test_contrato_repositorios.py) garantiza que con la base real funcionan igual.

Las reglas de QUIÉN puede hacer cada cosa (dueño del pedido, rol admin) se
quedan por ahora en la API (api/dependencies.py), porque dependen del token.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from libreria import pedidos
from libreria.excepciones import (
    PedidoNoEncontradoError,
    ServicioExternoError,
    UsuarioNoEncontradoError,
)
from libreria.modelos import Libro, Usuario
from libreria.pedidos import Pedido
from libreria.puertos import Notificador, UnidadDeTrabajo

log = logging.getLogger(__name__)

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

    def __init__(
        self, uow: UnidadDeTrabajo, notificador: Notificador, ahora: Reloj = datetime.now
    ) -> None:
        self._uow = uow
        self._notificador = notificador
        self._ahora = ahora

    def ejecutar(self, cmd: CrearPedidoComando) -> Pedido:
        with self._uow as uow:
            usuario = uow.usuarios.obtener(cmd.usuario_id)
            if usuario is None:
                raise UsuarioNoEncontradoError(f"No existe el usuario {cmd.usuario_id}")

            libros = uow.libros.obtener_varios(cmd.lineas)
            # El dominio valida y descuenta (o lanza el error sin tocar nada)
            pedido = pedidos.crear_pedido(cmd.usuario_id, cmd.lineas, libros, fecha=self._ahora())

            for isbn in cmd.lineas:
                uow.libros.guardar(libros[isbn])
            uow.pedidos.agregar(pedido)
            uow.confirmar()
            guardado = _releer(uow, pedido)

        _avisar(self._notificador, usuario, *_aviso_pedido_creado(guardado))
        return guardado


class CambiarEstatusPedido:
    """Avanza un pedido (pagado, enviado) o lo cancela regresando el stock."""

    def __init__(self, uow: UnidadDeTrabajo, notificador: Notificador) -> None:
        self._uow = uow
        self._notificador = notificador

    def ejecutar(self, cmd: CambiarEstatusComando) -> Pedido:
        with self._uow as uow:
            pedido = uow.pedidos.obtener(cmd.pedido_id)
            if pedido is None:
                raise PedidoNoEncontradoError(f"No existe el pedido {cmd.pedido_id}")

            libros = uow.libros.obtener_varios(item.isbn for item in pedido.items)
            anterior = pedido.estatus
            pedidos.cambiar_estatus(pedido, cmd.estatus, libros)  # el dominio decide

            cambio = pedido.estatus != anterior  # cancelar dos veces no cambia nada
            if cambio:
                for libro in libros.values():
                    uow.libros.guardar(libro)
                uow.pedidos.guardar(pedido)
                uow.confirmar()
            guardado = _releer(uow, pedido)
            usuario = uow.usuarios.obtener(pedido.usuario_id) if cambio else None

        if usuario is not None:  # solo se avisa si algo cambió
            _avisar(self._notificador, usuario, *_aviso_cambio_estatus(guardado))
        return guardado


class AgregarLibro:
    """Un administrador da de alta un libro. LibroDuplicadoError si el ISBN ya existe."""

    def __init__(self, uow: UnidadDeTrabajo) -> None:
        self._uow = uow

    def ejecutar(self, libro: Libro) -> Libro:
        with self._uow as uow:
            uow.libros.agregar(libro)
            uow.confirmar()
        return libro


# ── Avisos ───────────────────────────────────────────────────────────────────

_TEXTO_ESTATUS = {
    "pagado": "Recibimos tu pago. Pronto enviaremos tus libros.",
    "enviado": "Tus libros ya van en camino.",
    "cancelado": "Tu pedido fue cancelado y los libros regresaron al inventario.",
}


def _aviso_pedido_creado(pedido: Pedido) -> tuple[str, str]:
    """(asunto, mensaje) del aviso de pedido nuevo."""
    lineas = "\n".join(
        f"- {item.titulo} x{item.cantidad}: ${item.subtotal:.2f}" for item in pedido.items
    )
    mensaje = f"Tu pedido quedó registrado.\n{lineas}\nTotal: ${pedido.total:.2f}"
    return f"Recibimos tu pedido #{pedido.id}", mensaje


def _aviso_cambio_estatus(pedido: Pedido) -> tuple[str, str]:
    """(asunto, mensaje) del aviso de cambio de estatus."""
    return f"Tu pedido #{pedido.id} ahora está {pedido.estatus}", _TEXTO_ESTATUS[pedido.estatus]


def _avisar(notificador: Notificador, usuario: Usuario, asunto: str, mensaje: str) -> None:
    """Envía el aviso. Si falla, solo lo registra: el pedido ya quedó guardado."""
    try:
        notificador.enviar(usuario.email, asunto, mensaje)
    except ServicioExternoError as e:
        log.warning("No se pudo avisar a %s (%s): %s", usuario.email, asunto, e)


def _releer(uow: UnidadDeTrabajo, pedido: Pedido) -> Pedido:
    """El pedido tal como quedó guardado (con id y en el formato del repositorio)."""
    assert pedido.id is not None  # agregar() ya le asignó uno
    guardado = uow.pedidos.obtener(pedido.id)
    assert guardado is not None
    return guardado
