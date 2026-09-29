"""Puertos: lo que la lógica de negocio necesita del mundo exterior.

Un puerto describe QUÉ necesita el servicio, no CÓMO se hace. Las clases que
lo cumplen (los adaptadores) no heredan de él: basta con que tengan los mismos
métodos (tipado estructural). mypy verifica que efectivamente los tengan.

Este módulo solo importa modelos del dominio; nunca json, sqlalchemy ni httpx.

Hay dos familias de puertos:

- RepositorioCatalogo: el catálogo completo de la consola (un archivo JSON).
- RepositorioLibros, RepositorioPedidos, RepositorioUsuarios y UnidadDeTrabajo:
  lo que necesitarán los casos de uso de pedidos para trabajar con la base de
  datos sin conocer SQLAlchemy. Solo tienen los métodos que esos casos de uso
  van a usar (principio de segregación de interfaces).
"""

from collections.abc import Iterable
from types import TracebackType
from typing import Protocol, Self

from libreria.modelos import Libreria, Libro, Usuario
from libreria.pedidos import Pedido


class RepositorioCatalogo(Protocol):
    """Persistencia del catálogo completo de la librería.

    Contrato que todo adaptador debe respetar (principio de Liskov):
    - cargar() devuelve los libros ya validados como objetos Libro.
    - Si algo falla, ambos métodos lanzan LibreriaError (o una subclase),
      nunca OSError, json.JSONDecodeError ni errores de SQLAlchemy.
      ServicioCatalogo depende de esto para revertir los cambios.
    """

    def cargar(self) -> Libreria: ...

    def guardar(self, data: Libreria) -> None: ...


# ── Base de datos: repositorios + unidad de trabajo ─────────────────────────
# Contrato común de los tres repositorios:
# - Reciben y devuelven objetos del dominio (Libro, Pedido, Usuario), nunca
#   filas ni sesiones.
# - NUNCA confirman (commit): solo registran cambios. Confirmar o revertir le
#   toca a la UnidadDeTrabajo, para que varios cambios se guarden juntos o
#   ninguno (p. ej. descontar stock y crear el pedido).
# - Los errores de la base se traducen a LibreriaError.


class RepositorioLibros(Protocol):
    def obtener(self, isbn: str) -> Libro | None:
        """El libro con ese ISBN, o None si no existe."""
        ...

    def obtener_varios(self, isbns: Iterable[str]) -> dict[str, Libro]:
        """Los libros que existen, por ISBN. Los que no existen no aparecen."""
        ...

    def agregar(self, libro: Libro) -> None:
        """Registra un libro nuevo. LibroDuplicadoError si el ISBN ya existe."""
        ...

    def guardar(self, libro: Libro) -> None:
        """Guarda los cambios de un libro existente. LibroNoEncontradoError si no existe."""
        ...


class RepositorioPedidos(Protocol):
    def obtener(self, pedido_id: int) -> Pedido | None:
        """El pedido con ese id, o None si no existe."""
        ...

    def agregar(self, pedido: Pedido) -> None:
        """Registra un pedido nuevo y le asigna su id (pedido.id deja de ser None)."""
        ...

    def guardar(self, pedido: Pedido) -> None:
        """Guarda el estatus de un pedido existente. PedidoNoEncontradoError si no existe.

        Las líneas no se guardan: una vez creado, un pedido no cambia de libros.
        """
        ...


class RepositorioUsuarios(Protocol):
    def obtener(self, usuario_id: int) -> Usuario | None:
        """El usuario con ese id, o None si no existe."""
        ...


class UnidadDeTrabajo(Protocol):
    """Agrupa los repositorios y decide si sus cambios se guardan juntos o ninguno.

    Uso:

        with uow:
            libros = uow.libros.obtener_varios(...)
            ...
            uow.confirmar()        # si no se llama, los cambios se descartan

    Al salir del `with` se revierte todo lo que no se confirmó, también si hubo
    una excepción.
    """

    # Propiedades de solo lectura: así un adaptador puede declarar un tipo más
    # concreto (LibrosSQL) y mypy lo sigue aceptando.
    @property
    def libros(self) -> RepositorioLibros: ...

    @property
    def pedidos(self) -> RepositorioPedidos: ...

    @property
    def usuarios(self) -> RepositorioUsuarios: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        error: BaseException | None,
        traza: TracebackType | None,
    ) -> None: ...

    def confirmar(self) -> None:
        """Guarda de forma definitiva todos los cambios registrados."""
        ...

    def revertir(self) -> None:
        """Descarta los cambios que no se han confirmado."""
        ...
