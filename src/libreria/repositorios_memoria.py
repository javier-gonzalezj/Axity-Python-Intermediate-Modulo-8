"""Adaptadores en memoria de los puertos (ver puertos.py).

Sirven para probar servicios y casos de uso sin archivos ni base de datos:
son rápidos y fáciles de preparar. Para que esas pruebas no mientan, estos
adaptadores tienen que comportarse IGUAL que los reales (CatalogoJSON y
UnidadDeTrabajoSQL); eso lo garantizan las pruebas de contrato
(tests/test_contrato_*.py), que corren la misma batería contra ambos.

Dos detalles copian a los adaptadores reales a propósito:

- Siempre se guardan y se devuelven COPIAS. Un archivo o una base no se
  enteran si alguien modifica un objeto que ya leyó; aquí tampoco.
- La unidad de trabajo trabaja sobre una copia del estado confirmado:
  confirmar() la vuelve definitiva y revertir() la descarta.
"""

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from libreria.excepciones import (
    LibroInvalidoError,
    LibroNoEncontradoError,
    PedidoNoEncontradoError,
)
from libreria.modelos import Libreria, Libro, Usuario
from libreria.pedidos import Pedido
from libreria.puertos import RepositorioLibros, RepositorioPedidos, RepositorioUsuarios

# ── Catálogo (consola) ───────────────────────────────────────────────────────


class CatalogoEnMemoria:
    """Adaptador de RepositorioCatalogo que guarda el catálogo en una variable."""

    def __init__(self, data: Libreria) -> None:
        self._guardado = deepcopy(data)

    def cargar(self) -> Libreria:
        return deepcopy(self._guardado)

    def guardar(self, data: Libreria) -> None:
        self._guardado = deepcopy(data)


# ── Base de datos: repositorios + unidad de trabajo ─────────────────────────


@dataclass
class _Estado:
    """Todo lo que "hay en la base": libros, pedidos, usuarios y el último id."""

    libros: dict[str, Libro] = field(default_factory=dict)
    pedidos: dict[int, Pedido] = field(default_factory=dict)
    usuarios: dict[int, Usuario] = field(default_factory=dict)
    ultimo_id_pedido: int = 0

    def copiar_desde(self, otro: "_Estado") -> None:
        """Reemplaza el contenido por una copia independiente de `otro`."""
        self.libros = deepcopy(otro.libros)
        self.pedidos = deepcopy(otro.pedidos)
        self.usuarios = deepcopy(otro.usuarios)
        self.ultimo_id_pedido = otro.ultimo_id_pedido


class LibrosEnMemoria:
    """Adaptador de RepositorioLibros."""

    def __init__(self, estado: _Estado) -> None:
        self._estado = estado

    def obtener(self, isbn: str) -> Libro | None:
        libro = self._estado.libros.get(isbn)
        return libro.model_copy(deep=True) if libro else None

    def obtener_varios(self, isbns: Iterable[str]) -> dict[str, Libro]:
        return {
            isbn: self._estado.libros[isbn].model_copy(deep=True)
            for isbn in set(isbns)
            if isbn in self._estado.libros
        }

    def agregar(self, libro: Libro) -> None:
        if libro.isbn in self._estado.libros:
            raise LibroInvalidoError(f"Ya existe un libro con ISBN {libro.isbn}")
        self._estado.libros[libro.isbn] = libro.model_copy(deep=True)

    def guardar(self, libro: Libro) -> None:
        if libro.isbn not in self._estado.libros:
            raise LibroNoEncontradoError(f"No existe el libro {libro.isbn}")
        self._estado.libros[libro.isbn] = libro.model_copy(deep=True)


class PedidosEnMemoria:
    """Adaptador de RepositorioPedidos."""

    def __init__(self, estado: _Estado) -> None:
        self._estado = estado

    def obtener(self, pedido_id: int) -> Pedido | None:
        pedido = self._estado.pedidos.get(pedido_id)
        return pedido.model_copy(deep=True) if pedido else None

    def agregar(self, pedido: Pedido) -> None:
        self._estado.ultimo_id_pedido += 1  # como el autoincremento de la base
        pedido.id = self._estado.ultimo_id_pedido
        self._estado.pedidos[pedido.id] = pedido.model_copy(deep=True)

    def guardar(self, pedido: Pedido) -> None:
        guardado = self._estado.pedidos.get(pedido.id) if pedido.id is not None else None
        if guardado is None:
            raise PedidoNoEncontradoError(f"No existe el pedido {pedido.id}")
        guardado.estatus = pedido.estatus  # igual que en SQL: solo el estatus


class UsuariosEnMemoria:
    """Adaptador de RepositorioUsuarios."""

    def __init__(self, estado: _Estado) -> None:
        self._estado = estado

    def obtener(self, usuario_id: int) -> Usuario | None:
        usuario = self._estado.usuarios.get(usuario_id)
        return usuario.model_copy(deep=True) if usuario else None


class UnidadDeTrabajoEnMemoria:
    """Adaptador de UnidadDeTrabajo sin base de datos.

    Se prepara con los libros y usuarios iniciales (los usuarios deben tener id):

        uow = UnidadDeTrabajoEnMemoria(libros=[...], usuarios=[...])
    """

    def __init__(self, libros: Iterable[Libro] = (), usuarios: Iterable[Usuario] = ()) -> None:
        self._confirmado = _Estado()
        for libro in libros:
            self._confirmado.libros[libro.isbn] = libro.model_copy(deep=True)
        for usuario in usuarios:
            if usuario.id is None:
                raise ValueError(f"El usuario {usuario.email} necesita un id")
            self._confirmado.usuarios[usuario.id] = usuario.model_copy(deep=True)

        # Los repositorios trabajan sobre esta copia; nunca sobre _confirmado
        self._trabajo = _Estado()
        self._trabajo.copiar_desde(self._confirmado)

        # Anotar con el puerto hace que mypy verifique que cada adaptador lo cumple
        self.libros: RepositorioLibros = LibrosEnMemoria(self._trabajo)
        self.pedidos: RepositorioPedidos = PedidosEnMemoria(self._trabajo)
        self.usuarios: RepositorioUsuarios = UsuariosEnMemoria(self._trabajo)
        self.confirmaciones = 0  # útil en pruebas: ¿el caso de uso confirmó?

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        error: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        self.revertir()

    def confirmar(self) -> None:
        self._confirmado.copiar_desde(self._trabajo)
        self.confirmaciones += 1

    def revertir(self) -> None:
        self._trabajo.copiar_desde(self._confirmado)
