"""Reglas de pedidos: el dominio, sin base de datos ni HTTP.

Aquí vive todo lo que DECIDE sobre un pedido:

- qué hace falta para crearlo (libros, cantidades positivas, stock suficiente),
- a qué estatus puede pasar desde cada estatus (TRANSICIONES),
- qué pasa al cancelarlo (los ejemplares regresan al inventario).

basedatos.py solo traduce entre estas clases y las tablas, y la API solo
traduce entre HTTP y estas clases. Como aquí no hay entrada/salida, las reglas
se prueban con objetos en memoria (tests/test_pedidos.py), sin SQLite ni FastAPI.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from libreria.excepciones import (
    LibroInvalidoError,
    PedidoInvalidoError,
    TransicionEstatusError,
)
from libreria.modelos import Libro

Estatus = Literal["pendiente", "pagado", "enviado", "cancelado"]

# Estatus a los que puede pasar un pedido desde cada estatus
TRANSICIONES: Mapping[Estatus, frozenset[Estatus]] = {
    "pendiente": frozenset({"pagado", "cancelado"}),
    "pagado": frozenset({"enviado", "cancelado"}),
    "enviado": frozenset(),  # ya salió: no se puede cancelar
    "cancelado": frozenset(),
}


class PedidoItem(BaseModel):
    """Una línea del pedido: qué libro, cuántos y a qué precio se vendió."""

    model_config = ConfigDict(extra="forbid")

    isbn: str
    titulo: str
    cantidad: int = Field(gt=0)
    precio_unitario: float = Field(ge=0)  # precio AL MOMENTO de la compra

    @property
    def subtotal(self) -> float:
        return round(self.cantidad * self.precio_unitario, 2)


class Pedido(BaseModel):
    """Pedido de un usuario. Su estatus solo cambia con avanzar_a() o cancelar()."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: int | None = None  # None mientras no se guarde en la base
    usuario_id: int
    fecha: datetime
    estatus: Estatus = "pendiente"
    items: list[PedidoItem]

    @property
    def total(self) -> float:
        return round(sum(item.subtotal for item in self.items), 2)

    def puede_pasar_a(self, nuevo: str) -> bool:
        return nuevo in TRANSICIONES[self.estatus]

    def avanzar_a(self, nuevo: str) -> None:
        """pendiente -> pagado -> enviado.

        Para cancelar se usa cancelar(), porque además regresa el stock.
        Acepta str (no solo Estatus) para rechazar con el mismo error un
        estatus que ni siquiera existe.
        """
        if nuevo == "cancelado":
            raise TransicionEstatusError("Para cancelar un pedido usa cancelar()")
        self.estatus = self._verificar_transicion(nuevo)

    def cancelar(self, libros: Mapping[str, Libro]) -> bool:
        """Cancela el pedido y regresa sus ejemplares a `libros` (soft delete).

        `libros` debe incluir los libros del pedido, por ISBN. Cancelar un
        pedido ya cancelado no hace nada y devuelve False; uno enviado no se
        puede cancelar. Si algo falla, ni el pedido ni los libros cambian.
        """
        if self.estatus == "cancelado":
            return False
        if not self.puede_pasar_a("cancelado"):
            raise TransicionEstatusError(
                f"El pedido {self.id} ya está '{self.estatus}'; no se puede cancelar"
            )
        faltantes = [item.isbn for item in self.items if item.isbn not in libros]
        if faltantes:
            raise LibroInvalidoError(f"Faltan libros del pedido: {', '.join(faltantes)}")

        for item in self.items:
            libros[item.isbn].reponer(item.cantidad)
        self.estatus = "cancelado"
        return True

    def _verificar_transicion(self, nuevo: str) -> Estatus:
        """Devuelve `nuevo` ya como Estatus si la transición está permitida."""
        for permitido in TRANSICIONES[self.estatus]:
            if permitido == nuevo:
                return permitido
        raise TransicionEstatusError(
            f"El pedido {self.id} no puede pasar de '{self.estatus}' a '{nuevo}'"
        )


def crear_pedido(
    usuario_id: int,
    lineas: Mapping[str, int],
    libros: Mapping[str, Libro],
    fecha: datetime,
) -> Pedido:
    """Arma un pedido nuevo a partir de {isbn: cantidad} y descuenta el stock.

    `libros` son los libros disponibles por ISBN (basta con los del pedido).
    Primero se validan TODAS las líneas y solo después se tocan los libros: si
    una línea falla, ningún libro queda descontado (todo o nada).

    La fecha se recibe en lugar de usar datetime.now(): así el dominio no
    depende del reloj y las pruebas pueden fijarla.
    """
    if not lineas:
        raise PedidoInvalidoError("El pedido no tiene libros")

    for isbn, cantidad in lineas.items():
        if cantidad <= 0:
            raise PedidoInvalidoError(f"Cantidad inválida para {isbn}: {cantidad}")
        libro = libros.get(isbn)
        if libro is None:
            raise LibroInvalidoError(f"No existe el libro {isbn}")
        libro.verificar_stock(cantidad)

    items: list[PedidoItem] = []
    for isbn, cantidad in lineas.items():
        libro = libros[isbn]
        libro.retirar(cantidad)
        items.append(
            PedidoItem(
                isbn=isbn, titulo=libro.titulo, cantidad=cantidad, precio_unitario=libro.precio
            )
        )
    return Pedido(usuario_id=usuario_id, fecha=fecha, items=items)


def cambiar_estatus(pedido: Pedido, nuevo: str, libros: Mapping[str, Libro]) -> None:
    """Punto único para cambiar el estatus: cancelar regresa stock, lo demás solo avanza."""
    if nuevo == "cancelado":
        pedido.cancelar(libros)
    else:
        pedido.avanzar_a(nuevo)
