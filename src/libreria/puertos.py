"""Puertos: lo que la lógica de negocio necesita del mundo exterior.

Un puerto describe QUÉ necesita el servicio, no CÓMO se hace. Las clases que
lo cumplen (los adaptadores) no heredan de él: basta con que tengan los mismos
métodos (tipado estructural). mypy verifica que efectivamente los tengan.

Este módulo solo importa modelos del dominio; nunca json, sqlalchemy ni httpx.
"""

from typing import Protocol

from libreria.modelos import Libreria


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
