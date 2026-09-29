"""Adaptadores SQLAlchemy de los puertos de la base de datos (ver puertos.py).

Cada repositorio traduce entre filas (LibroDB, PedidoDB, ...) y objetos del
dominio (Libro, Pedido, Usuario). Ninguno hace commit: los tres comparten la
sesión de UnidadDeTrabajoSQL, que es la única que confirma o revierte.

Las tablas y las funciones de traducción viven en basedatos.py; aquí se
reutilizan para no escribirlas dos veces.
"""

import logging
from collections.abc import Iterable
from types import TracebackType
from typing import Self

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from libreria.basedatos import (
    LibroDB,
    PedidoDB,
    PedidoItemDB,
    UsuarioDB,
    fila_a_libro,
    fila_a_pedido,
    fila_a_usuario,
    libro_a_fila,
)
from libreria.excepciones import (
    LibroDuplicadoError,
    LibroNoEncontradoError,
    PedidoNoEncontradoError,
    PersistenciaError,
)
from libreria.modelos import Libro, Usuario
from libreria.pedidos import Pedido
from libreria.puertos import RepositorioLibros, RepositorioPedidos, RepositorioUsuarios

log = logging.getLogger(__name__)


class LibrosSQL:
    """Adaptador de RepositorioLibros."""

    def __init__(self, sesion: Session) -> None:
        self._sesion = sesion

    def obtener(self, isbn: str) -> Libro | None:
        fila = self._sesion.get(LibroDB, isbn)
        return fila_a_libro(fila) if fila else None

    def obtener_varios(self, isbns: Iterable[str]) -> dict[str, Libro]:
        buscados = set(isbns)
        if not buscados:
            return {}
        # Una sola consulta (WHERE isbn IN (...)) en lugar de una por libro
        filas = self._sesion.scalars(select(LibroDB).where(LibroDB.isbn.in_(buscados)))
        return {fila.isbn: fila_a_libro(fila) for fila in filas}

    def agregar(self, libro: Libro) -> None:
        if self._sesion.get(LibroDB, libro.isbn) is not None:
            raise LibroDuplicadoError(f"Ya existe un libro con ISBN {libro.isbn}")
        self._sesion.add(libro_a_fila(libro))

    def guardar(self, libro: Libro) -> None:
        fila = self._sesion.get(LibroDB, libro.isbn)
        if fila is None:
            raise LibroNoEncontradoError(f"No existe el libro {libro.isbn}")
        # dominio -> fila (en_stock no se guarda: se deriva de cantidad_disponible)
        fila.titulo = libro.titulo
        fila.autor_nombre = libro.autor.nombre
        fila.autor_nacionalidad = libro.autor.nacionalidad
        fila.generos = list(libro.genero)
        fila.año_publicacion = libro.año_publicacion
        fila.precio = libro.precio
        fila.cantidad_disponible = libro.cantidad_disponible
        fila.editorial = libro.editorial


class PedidosSQL:
    """Adaptador de RepositorioPedidos."""

    def __init__(self, sesion: Session) -> None:
        self._sesion = sesion

    def obtener(self, pedido_id: int) -> Pedido | None:
        fila = self._sesion.get(PedidoDB, pedido_id)
        return fila_a_pedido(fila) if fila else None

    def agregar(self, pedido: Pedido) -> None:
        fila = PedidoDB(
            usuario_id=pedido.usuario_id,
            fecha=pedido.fecha,
            estatus=pedido.estatus,
            items=[
                PedidoItemDB(
                    isbn=item.isbn,
                    cantidad=item.cantidad,
                    precio_unitario=item.precio_unitario,
                )
                for item in pedido.items
            ],
        )
        self._sesion.add(fila)
        try:
            # flush envía el INSERT sin confirmarlo: así la base asigna el id,
            # pero todo se sigue pudiendo revertir.
            self._sesion.flush()
        except SQLAlchemyError as e:
            raise PersistenciaError(f"No se pudo registrar el pedido: {e}") from e
        pedido.id = fila.id

    def guardar(self, pedido: Pedido) -> None:
        fila = self._sesion.get(PedidoDB, pedido.id) if pedido.id is not None else None
        if fila is None:
            raise PedidoNoEncontradoError(f"No existe el pedido {pedido.id}")
        fila.estatus = pedido.estatus


class UsuariosSQL:
    """Adaptador de RepositorioUsuarios."""

    def __init__(self, sesion: Session) -> None:
        self._sesion = sesion

    def obtener(self, usuario_id: int) -> Usuario | None:
        fila = self._sesion.get(UsuarioDB, usuario_id)
        return fila_a_usuario(fila) if fila else None


class UnidadDeTrabajoSQL:
    """Adaptador de UnidadDeTrabajo sobre una Session de SQLAlchemy.

    No crea ni cierra la sesión: la recibe ya abierta (en la API vendrá de
    obtener_sesion() y en las pruebas, de una base en memoria). Su trabajo es
    decidir entre commit y rollback. Mientras se use, esa sesión solo debe
    tocarse a través de los repositorios, porque al salir del `with` se revierte
    todo lo que no se haya confirmado.
    """

    def __init__(self, sesion: Session) -> None:
        self._sesion = sesion
        # Anotar con el puerto hace que mypy verifique que cada adaptador lo cumple
        self.libros: RepositorioLibros = LibrosSQL(sesion)
        self.pedidos: RepositorioPedidos = PedidosSQL(sesion)
        self.usuarios: RepositorioUsuarios = UsuariosSQL(sesion)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        error: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        # Después de confirmar() no queda nada pendiente, así que esto no borra
        # nada guardado; si hubo una excepción o se olvidó confirmar, descarta.
        self.revertir()

    def confirmar(self) -> None:
        try:
            self._sesion.commit()
        except SQLAlchemyError as e:
            self._sesion.rollback()
            log.warning("No se pudo confirmar; se revirtieron los cambios: %s", e)
            raise PersistenciaError(f"No se pudieron guardar los cambios: {e}") from e

    def revertir(self) -> None:
        self._sesion.rollback()
